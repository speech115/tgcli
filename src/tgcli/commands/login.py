"""Authorize a session via QR or phone+code (ADR-0042)."""

from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from telethon import errors as telethon_errors

from tgcli import authclient, desktop, login_state, safety
from tgcli.commands.accounts import _append_config_block
from tgcli.config import Account, Config, default_config_path, load_config
from tgcli.errors import (
    ConfigError,
    NotFoundError,
    PolicyError,
    RateLimitError,
    TgcliError,
)
from tgcli.formatting import mask_phone
from tgcli.output import note
from tgcli.session import state_dir


class LoginTimeoutError(TgcliError):
    exit_code = 1
    code = "TIMEOUT"


_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_new_alias(alias: str) -> None:
    if not alias or not _ALIAS_RE.match(alias):
        raise ConfigError(
            f"invalid account alias {alias!r}; "
            "use letters, digits, underscore, or hyphen only"
        )


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
        )
    ]


def _session_stem(config: Config, alias: str) -> str:
    account = config.accounts.get(alias)
    return account.session if account is not None else alias


def _destination_path(session_stem: str) -> Path:
    return state_dir() / "sessions" / f"{session_stem}.session"


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
) -> None:
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
) -> None:
    details: dict = {"method": method, "outcome": outcome}
    if phone:
        details["phone"] = mask_phone(phone)
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
) -> dict:
    me = await client.get_me()
    # Disconnect before promote so SQLite releases the staged file.
    await client.disconnect()
    _audit_login(alias, method=method, outcome="authorized", phone=phone)
    backup = login_state.promote(login_id, dest, keep_backup=keep_backup)
    if is_new:
        _append_config_block(default_config_path(), alias, api_id, api_hash)
    return {
        "alias": alias,
        "method": method,
        "status": "authorized",
        "next": None,
        "user": _user_dict(me),
        "session": str(dest),
        "backup": str(backup) if backup else None,
    }


def _pending(attempt: dict, *, next_step: str) -> dict:
    return {
        "alias": attempt["alias"],
        "method": attempt["method"],
        "status": "pending",
        "next": next_step,
        "login_id": attempt["login_id"],
        "expires_at": attempt["expires_at"],
    }


async def start_login(
    config: Config,
    alias: str,
    *,
    phone: str | None,
    api_id: int | None,
    api_hash: str | None,
    force: bool,
    timeout: float,
    qr_format: str,
    password_stdin: bool,
) -> dict:
    resolved_id, resolved_hash, is_new = _resolve_credentials(
        config, alias, api_id=api_id, api_hash=api_hash
    )
    await _probe_if_needed(
        config, alias, force=force, api_id=resolved_id, api_hash=resolved_hash
    )
    if is_new:
        _validate_new_alias(alias)
    method = "phone" if phone else "qr"
    _audit_login(alias, method=method, outcome="started", phone=phone)
    attempt = login_state.create_attempt(
        alias,
        method,
        api_id=resolved_id,
        api_hash=resolved_hash,
        phone=phone,
    )
    login_id = attempt["login_id"]
    staged = login_state.staged_session_path(login_id)
    dest = _destination_path(_session_stem(config, alias))
    # Backup whenever the destination exists — including an orphan session for
    # a not-yet-configured alias (e.g. promote succeeded, config append failed).
    keep_backup = dest.exists()

    try:
        async with authclient.unauthorized_client(
            staged, resolved_id, resolved_hash
        ) as client:
            if method == "phone":
                assert phone is not None
                return await _phone_start(client, attempt, phone=phone)
            return await _qr_wait(
                client,
                attempt,
                is_new=is_new,
                api_id=resolved_id,
                api_hash=resolved_hash,
                dest=dest,
                keep_backup=keep_backup,
                timeout=timeout,
                qr_format=qr_format,
                password_stdin=password_stdin,
            )
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"FLOOD_WAIT; retry after {exc.seconds}s",
            retry_after=exc.seconds,
        ) from exc
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
    login_state.update_attempt(attempt["login_id"], phone_code_hash=phone_code_hash)
    note(
        f"confirmation code sent to {mask_phone(phone)}; "
        f"continue with: tg accounts login --continue {attempt['login_id']} --code …"
    )
    return _pending(attempt, next_step="code")


def _emit_qr(qr, *, qr_format: str) -> None:
    url = qr.url
    if qr_format == "text":
        token = url.split("token=", 1)[-1] if "token=" in url else url
        note(f"QR login token: {token}")
        note("Confirm this login in Telegram, or use --qr-format link.")
        return
    note(f"Open this link in Telegram to confirm login:\n{url}")
    if desktop.open_url(url):
        note("opened tg:// login link in the native client")
    else:
        note("could not open tg:// link automatically; paste it into Telegram")


async def _qr_wait(
    client,
    attempt: dict,
    *,
    is_new: bool,
    api_id: int,
    api_hash: str,
    dest: Path,
    keep_backup: bool,
    timeout: float,
    qr_format: str,
    password_stdin: bool,
) -> dict:
    login_id = attempt["login_id"]
    alias = attempt["alias"]
    qr = await client.qr_login()
    _emit_qr(qr, qr_format=qr_format)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LoginTimeoutError(
                f"QR login timed out after {timeout}s; start login again",
                login_id=login_id,
            )
        expires_in = (qr.expires - datetime.now(UTC)).total_seconds()
        wait_timeout = min(remaining, max(expires_in, 0.1))
        try:
            await qr.wait(timeout=wait_timeout)
            break
        except asyncio.TimeoutError:
            if time.monotonic() >= deadline:
                raise LoginTimeoutError(
                    f"QR login timed out after {timeout}s; start login again",
                    login_id=login_id,
                ) from None
            await qr.recreate()
            _emit_qr(qr, qr_format=qr_format)
        except telethon_errors.SessionPasswordNeededError:
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
            except telethon_errors.FloodWaitError as exc:
                raise RateLimitError(
                    f"FLOOD_WAIT; retry after {exc.seconds}s",
                    retry_after=exc.seconds,
                ) from exc
            break

    return await _finish_authorized(
        client,
        login_id=login_id,
        alias=alias,
        method="qr",
        phone=None,
        is_new=is_new,
        api_id=api_id,
        api_hash=api_hash,
        dest=dest,
        keep_backup=keep_backup,
    )


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
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"FLOOD_WAIT; retry after {exc.seconds}s",
            retry_after=exc.seconds,
        ) from exc
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
    staged = login_state.staged_session_path(login_id)
    config = load_config()
    dest = _destination_path(_session_stem(config, alias))
    keep_backup = dest.exists()
    is_new = alias not in config.accounts
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
        elif method == "phone":
            # Phone path: submit confirmation code first.
            resolved_code = _collect_code(code=code)
            phone = attempt["phone"]
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
            except telethon_errors.FloodWaitError as exc:
                raise RateLimitError(
                    f"FLOOD_WAIT; retry after {exc.seconds}s",
                    retry_after=exc.seconds,
                ) from exc
        else:
            raise NotFoundError(
                f"login_id {login_id!r} is not awaiting a password; start login again"
            )

        return await _finish_authorized(
            client,
            login_id=login_id,
            alias=alias,
            method=method,
            phone=attempt.get("phone"),
            is_new=is_new,
            api_id=attempt["api_id"],
            api_hash=attempt["api_hash"],
            dest=dest,
            keep_backup=keep_backup,
        )
