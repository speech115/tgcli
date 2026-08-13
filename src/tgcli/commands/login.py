"""Authorize a session via phone, code, and optional password (ADR-0088)."""

from __future__ import annotations

import sys
from pathlib import Path

from telethon import errors as telethon_errors

from tgcli import authclient, desktop, login_state, safety
from tgcli.commands.accounts import _append_config_block
from tgcli.config import (
    Account,
    Config,
    default_config_path,
    load_config,
    validate_alias,
    validate_role_name,
)
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli.formatting import mask_phone
from tgcli.output import note
from tgcli.session import session_path, state_dir


def login_rows(data: dict) -> list[tuple]:
    return [
        (
            data.get("alias"),
            data.get("method"),
            data.get("status"),
            data.get("next"),
            data.get("login_id"),
            (data.get("user") or {}).get("phone"),
            data.get("session"),
            data.get("role"),
        )
    ]


def _session_stem(config: Config, alias: str) -> str:
    account = config.accounts.get(alias)
    return account.session if account is not None else alias


def _destination_path(session_stem: str) -> Path:
    return state_dir() / "sessions" / f"{session_stem}.session"


def _login_destination(config: Config, alias: str, role: str | None) -> Path:
    if role is None:
        return _destination_path(_session_stem(config, alias))
    account = config.accounts.get(alias)
    if account is None:
        raise ConfigError(
            f"account {alias!r} is not configured; add it before authorizing a role"
        )
    validate_role_name(role)
    return session_path(account, role)


def _resolve_credentials(
    config: Config,
    alias: str,
    *,
    api_id: int | None,
    api_hash: str | None,
) -> tuple[int, str, bool]:
    existing = config.accounts.get(alias)
    if existing is not None:
        if api_id is not None or api_hash is not None:
            raise ConfigError(
                f"account {alias!r} already configured; do not pass --api-id/--api-hash"
            )
        return existing.api_id, existing.api_hash, False
    if api_id is None or api_hash is None:
        raise ConfigError(
            f"unknown account {alias!r}; pass both --api-id and --api-hash"
        )
    return api_id, api_hash, True


async def _probe_if_needed(
    config: Config,
    alias: str,
    *,
    force: bool,
    api_id: int,
    api_hash: str,
    role: str | None = None,
) -> None:
    if role is not None:
        account = config.accounts.get(alias)
        if account is None:
            return
        path = session_path(account, role)
        if not path.is_file():
            return
        if await authclient.probe_authorized(account, role=role):
            if not force:
                raise PolicyError(
                    f"session role {role!r} for account {alias!r} is still authorized; "
                    "pass --force to replace it"
                )
        return
    path = _destination_path(_session_stem(config, alias))
    if not path.is_file():
        return
    account = config.accounts.get(alias)
    if account is None:
        # Orphan session for a not-yet-configured alias: probe with the
        # credentials the caller just supplied so we never overwrite a still-
        # authorized key without --force.
        account = Account(alias=alias, api_id=api_id, api_hash=api_hash, session=alias)
    if await authclient.probe_authorized(account):
        if not force:
            raise PolicyError(
                f"session {alias!r} is still authorized; pass --force to replace it"
            )


def _collect_password(*, password_stdin: bool) -> str | None:
    if password_stdin:
        return sys.stdin.readline().rstrip("\n")
    if desktop.dialog_available():
        return desktop.ask_secret("tgcli", "Telegram cloud password", hidden=True)
    return None


def _collect_code(*, code: str | None) -> str:
    if code == "-":
        value = sys.stdin.readline().rstrip("\n")
    elif code is not None:
        value = code
    elif desktop.dialog_available():
        value = desktop.ask_secret("tgcli", "Telegram confirmation code", hidden=False)
    else:
        raise ConfigError("confirmation code required; pass --code VALUE or --code -")
    if not value:
        raise ConfigError("confirmation code required; pass --code VALUE or --code -")
    return value


def _user_dict(me) -> dict:
    phone = getattr(me, "phone", None)
    if phone and not str(phone).startswith("+"):
        phone = f"+{phone}"
    return {
        "id": getattr(me, "id", None),
        "username": getattr(me, "username", None),
        "phone": mask_phone(phone) if phone else None,
    }


def _audit_login(
    alias: str,
    *,
    method: str,
    outcome: str,
    phone: str | None = None,
    role: str | None = None,
) -> None:
    details: dict = {"method": method, "outcome": outcome}
    if phone:
        details["phone"] = mask_phone(phone)
    if role is not None:
        details["role"] = role
    safety.append_audit("accounts-login", alias, details)


async def _sign_in_password(client, password: str) -> None:
    await client.sign_in(password=password)


async def _finish_authorized(
    client,
    *,
    login_id: str,
    alias: str,
    method: str,
    phone: str | None,
    is_new: bool,
    api_id: int,
    api_hash: str,
    dest: Path,
    keep_backup: bool,
    role: str | None = None,
) -> dict:
    me = await client.get_me()
    # Disconnect before promote so SQLite releases the staged file.
    await client.disconnect()
    _audit_login(alias, method=method, outcome="authorized", phone=phone, role=role)
    backup = login_state.promote(login_id, dest, keep_backup=keep_backup)
    if is_new and role is None:
        _append_config_block(default_config_path(), alias, api_id, api_hash)
    result = {
        "alias": alias,
        "method": method,
        "status": "authorized",
        "next": None,
        "user": _user_dict(me),
        "session": str(dest),
        "backup": str(backup) if backup else None,
    }
    if role is not None:
        result["role"] = role
    return result


def _pending(attempt: dict, *, next_step: str) -> dict:
    result = {
        "alias": attempt["alias"],
        "method": attempt["method"],
        "status": "pending",
        "next": next_step,
        "login_id": attempt["login_id"],
        "expires_at": attempt["expires_at"],
    }
    role = attempt.get("role")
    if role is not None:
        result["role"] = role
    return result


async def start_login(
    config: Config,
    alias: str,
    *,
    phone: str,
    api_id: int | None,
    api_hash: str | None,
    force: bool,
    role: str | None = None,
) -> dict:
    if role is not None:
        validate_role_name(role)
        if alias not in config.accounts:
            raise ConfigError(
                f"account {alias!r} is not configured; add it before authorizing a role"
            )
    resolved_id, resolved_hash, is_new = _resolve_credentials(
        config, alias, api_id=api_id, api_hash=api_hash
    )
    if role is not None and is_new:
        raise ConfigError(
            f"account {alias!r} is not configured; add it before authorizing a role"
        )
    await _probe_if_needed(
        config,
        alias,
        force=force,
        api_id=resolved_id,
        api_hash=resolved_hash,
        role=role,
    )
    if is_new:
        validate_alias(alias)
    method = "phone"
    _audit_login(alias, method=method, outcome="started", phone=phone, role=role)
    attempt = login_state.create_attempt(
        alias,
        method,
        api_id=resolved_id,
        api_hash=resolved_hash,
        role=role,
    )
    login_id = attempt["login_id"]
    staged = login_state.staged_session_path(login_id)

    try:
        async with authclient.unauthorized_client(
            staged, resolved_id, resolved_hash
        ) as client:
            return await _phone_start(client, attempt, phone=phone)
    except (
        telethon_errors.PhoneNumberBannedError,
        telethon_errors.PhoneNumberInvalidError,
    ) as exc:
        login_state.discard_attempt(login_id)
        raise ConfigError(str(exc)) from exc


async def _phone_start(client, attempt: dict, *, phone: str) -> dict:
    sent = await client.send_code_request(phone)
    phone_code_hash = sent.phone_code_hash
    if not isinstance(phone, str) or not isinstance(phone_code_hash, str):
        raise ConfigError("Telegram returned unexpected send_code_request types")
    login_state.save_phone(attempt["login_id"], phone)
    login_state.update_attempt(attempt["login_id"], phone_code_hash=phone_code_hash)
    note(
        f"confirmation code sent to {mask_phone(phone)}; "
        f"continue with: tg accounts login --continue {attempt['login_id']} --code …"
    )
    return _pending(attempt, next_step="code")


async def _complete_password(
    client,
    attempt: dict,
    *,
    password_stdin: bool,
) -> dict | None:
    """Return a pending document, or None after a successful sign-in."""
    login_id = attempt["login_id"]
    password = _collect_password(password_stdin=password_stdin)
    if password is None:
        login_state.update_attempt(login_id, next="password")
        note(
            f"cloud password required; continue with: "
            f"tg accounts login --continue {login_id} --password-stdin"
        )
        return _pending(attempt, next_step="password")
    try:
        await _sign_in_password(client, password)
    except telethon_errors.PasswordHashInvalidError as exc:
        login_state.update_attempt(login_id, next="password")
        raise ConfigError("invalid cloud password") from exc
    return None


async def continue_login(
    *,
    login_id: str,
    code: str | None,
    password_stdin: bool,
) -> dict:
    attempt = login_state.load_attempt(login_id)
    alias = attempt["alias"]
    method = attempt["method"]
    if method != "phone":
        raise NotFoundError(
            f"login_id {login_id!r} uses a removed login method; start login again"
        )
    role = attempt.get("role")
    phone = login_state.load_phone(login_id)
    staged = login_state.staged_session_path(login_id)
    config = load_config()
    dest = _login_destination(config, alias, role)
    keep_backup = dest.exists()
    is_new = alias not in config.accounts and role is None
    needs_password = attempt.get("next") == "password"

    async with authclient.unauthorized_client(
        staged, attempt["api_id"], attempt["api_hash"]
    ) as client:
        if needs_password:
            pending = await _complete_password(
                client, attempt, password_stdin=password_stdin
            )
            if pending is not None:
                return pending
        else:
            # Submit the phone confirmation code first.
            resolved_code = _collect_code(code=code)
            phone_code_hash = attempt["phone_code_hash"]
            if not isinstance(phone, str) or not isinstance(phone_code_hash, str):
                raise ConfigError("login attempt missing phone state")
            if not isinstance(resolved_code, str):
                raise ConfigError("code must be a string")
            try:
                await client.sign_in(
                    phone, resolved_code, phone_code_hash=phone_code_hash
                )
            except telethon_errors.SessionPasswordNeededError:
                pending = await _complete_password(
                    client, attempt, password_stdin=password_stdin
                )
                if pending is not None:
                    return pending
            except (
                telethon_errors.PhoneCodeInvalidError,
                telethon_errors.PhoneCodeEmptyError,
            ) as exc:
                raise ConfigError("invalid confirmation code") from exc
            except telethon_errors.PhoneCodeExpiredError as exc:
                login_state.discard_attempt(login_id)
                raise ConfigError(
                    "confirmation code expired; start login again"
                ) from exc
        return await _finish_authorized(
            client,
            login_id=login_id,
            alias=alias,
            method=method,
            phone=phone,
            is_new=is_new,
            api_id=attempt["api_id"],
            api_hash=attempt["api_hash"],
            dest=dest,
            keep_backup=keep_backup,
            role=role,
        )
