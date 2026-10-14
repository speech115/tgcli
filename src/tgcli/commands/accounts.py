import json
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from tgcli import atomic, safety, session
from tgcli.config import (
    Account,
    Config,
    default_config_path,
    load_config,
    validate_role_name,
)
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli.output import note


def list_accounts(config: Config) -> dict:
    return {
        "default_account": config.default_account,
        "accounts": [
            {"alias": account.alias, "session": account.session}
            for account in config.accounts.values()
        ],
    }


def to_rows(data: dict) -> list[tuple]:
    return [(entry["alias"], entry["session"]) for entry in data["accounts"]]


def show_account(config: Config, alias: str) -> dict:
    account = config.accounts.get(alias)
    if account is None:
        raise NotFoundError(f"unknown account alias: {alias!r}")
    path = session.session_path(account)
    bak = Path(str(path) + ".bak")
    exists = path.is_file()
    bytes_count: int | None = None
    modified: str | None = None
    if exists:
        stat = path.stat()
        bytes_count = stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    roles = []
    for role_name in session.list_roles(account):
        role_path = session.session_path(account, role=role_name)
        roles.append(
            {
                "name": role_name,
                "session": str(role_path),
                "exists": role_path.is_file(),
                "locked": session.lock_held(role_path) is True,
                "authorized": None,
            }
        )
    return {
        "alias": alias,
        "in_config": True,
        "session": str(path),
        "exists": exists,
        "bytes": bytes_count,
        "modified": modified,
        "locked": session.lock_held(path) is True,
        "backup": str(bak) if bak.is_file() else None,
        "authorized": None,
        "roles": roles,
    }


def show_rows(data: dict) -> list[tuple]:
    return [
        (
            data["alias"],
            data["exists"],
            data["bytes"],
            data["modified"],
            data["locked"],
            data["backup"],
            data["authorized"],
        )
    ]


def _table_path(line: str) -> tuple[str, ...] | None:
    """The key path a `[table]` header line opens, or None for other lines."""
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    try:
        parsed: object = tomllib.loads(stripped)
    except tomllib.TOMLDecodeError:
        return None  # a line inside a multi-line array, not a header
    path: list[str] = []
    while isinstance(parsed, dict) and len(parsed) == 1:
        key, parsed = next(iter(parsed.items()))
        path.append(key)
    return tuple(path)


def _remove_config_block(config_path: Path, alias: str) -> None:
    """Drop `[accounts.<alias>]` and its subtables, keeping unrelated text and
    comments. Refuses, writing nothing, unless the result parses without the
    alias: the caller deletes sessions only after this returns."""
    text = config_path.read_text()
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        path = _table_path(line)
        if path is not None:
            skipping = path[:2] == ("accounts", alias)
        if not skipping:
            kept.append(line)
    result = "".join(kept)
    try:
        remaining = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"removing [accounts.{alias}] would break config: {exc}"
        ) from exc
    # The scan is line based: a header-like line inside a multi-line string
    # would cut the wrong text. Write only when exactly the alias is gone.
    expected = tomllib.loads(text)
    expected.get("accounts", {}).pop(alias, None)
    if _without_empty_accounts(remaining) != _without_empty_accounts(expected):
        raise ConfigError(
            f"cannot remove only the [accounts.{alias}] table from {config_path}; "
            "remove the account there by hand"
        )
    atomic.replace_text(config_path, result)


def _without_empty_accounts(document: dict) -> dict:
    if document.get("accounts") == {}:
        return {key: value for key, value in document.items() if key != "accounts"}
    return document


def remove_account(
    config: Config,
    alias: str,
    *,
    confirm: bool,
    keep_session: bool,
    role: str | None = None,
) -> dict:
    if alias not in config.accounts:
        raise NotFoundError(f"unknown account alias: {alias!r}")
    if role is not None:
        return _remove_role(
            config, alias, role=role, confirm=confirm, keep_session=keep_session
        )
    if not confirm:
        note(f"refusing to remove account {alias!r}; re-run with --confirm")
        raise PolicyError(f"refusing to remove account {alias!r} without --confirm")
    if config.default_account == alias:
        raise PolicyError(
            f"refusing to remove default_account {alias!r}; "
            "edit default_account in config first"
        )
    account = config.accounts[alias]
    path = session.session_path(account)
    bak = Path(str(path) + ".bak")
    # Role sessions are full-access keys of the same account: they go with it,
    # since `--role` removal is unreachable once the config block is gone.
    role_paths = session.role_session_files(account)
    session.ensure_state_dir("sessions")
    with ExitStack() as locks:
        for locked in (path, *role_paths.values()):
            locks.enter_context(
                session.session_file_lock(locked, busy_error=PolicyError)
            )
        session_existed = path.is_file()
        backup_existed = bak.is_file()

        def _status(existed: bool) -> str:
            if keep_session:
                return "kept" if existed else "absent"
            return "deleted" if existed else "absent"

        session_status = _status(session_existed)
        backup_status = _status(backup_existed)
        role_status = {role: _status(True) for role in role_paths}

        safety.append_audit(
            "accounts-remove",
            alias,
            {
                "session": session_status,
                "backup": backup_status,
                "roles": role_status,
                "keep_session": keep_session,
            },
        )

        config_path = default_config_path()
        _remove_config_block(config_path, alias)
        if not keep_session:
            for removed in (path, *role_paths.values()):
                _unlink_with_backup(removed)
        role_status.update(
            _remove_late_roles(alias, account, role_paths, keep_session=keep_session)
        )

        return {
            "alias": alias,
            "config": "removed",
            "session": session_status,
            "backup": backup_status,
            "roles": role_status,
        }


def _unlink_with_backup(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(str(path) + ".bak").unlink(missing_ok=True)


def _remove_late_roles(
    alias: str, account: Account, known: dict[str, Path], *, keep_session: bool
) -> dict[str, str]:
    """Roles a concurrent login promoted after the first scan. Scanning again
    once the config block is gone pairs with the login's own config re-check
    after it promotes: whichever runs second sees the other's write."""
    late = {
        role: role_path
        for role, role_path in session.role_session_files(account).items()
        if role not in known
    }
    if not late:
        return {}
    status = "kept" if keep_session else "deleted"
    late_status = dict.fromkeys(late, status)
    if keep_session:
        return late_status
    safety.append_audit(
        "accounts-remove", alias, {"roles": late_status, "keep_session": False}
    )
    for role_path in late.values():
        with session.session_file_lock(role_path, busy_error=PolicyError):
            _unlink_with_backup(role_path)
    return late_status


def _remove_role(
    config: Config,
    alias: str,
    *,
    role: str,
    confirm: bool,
    keep_session: bool,
) -> dict:
    validate_role_name(role)
    if keep_session:
        raise PolicyError("accounts remove --role rejects --keep-session")
    if not confirm:
        note(
            f"refusing to remove session role {role!r} for account {alias!r}; "
            "re-run with --confirm"
        )
        raise PolicyError(
            f"refusing to remove session role {role!r} for account {alias!r} "
            "without --confirm"
        )
    account = config.accounts[alias]
    path = session.session_path(account, role=role)
    if not path.is_file():
        raise NotFoundError(
            f"session role {role!r} for account {alias!r} does not exist"
        )
    bak = Path(str(path) + ".bak")
    session.ensure_state_dir("sessions")
    with session.session_file_lock(path, busy_error=PolicyError):
        backup_existed = bak.is_file()
        safety.append_audit(
            "accounts-remove-role",
            alias,
            {
                "role": role,
                "session": "deleted",
                "backup": "deleted" if backup_existed else "absent",
            },
        )
        path.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        return {
            "alias": alias,
            "role": role,
            "config": "unchanged",
            "session": "deleted",
            "backup": "deleted" if backup_existed else "absent",
        }


def remove_rows(data: dict) -> list[tuple]:
    return [
        (
            data["alias"],
            data["config"],
            data["session"],
            data["backup"],
            data.get("role"),
        )
    ]


def _reject_colliding_alias(config_path: Path, alias: str) -> None:
    if not config_path.exists():
        return
    wanted = alias.casefold()
    for existing in load_config(config_path).accounts.values():
        if existing.session.casefold() == wanted:
            raise ConfigError(
                f"account {alias!r} would share session file "
                f"{existing.session!r} with account {existing.alias!r} "
                "on a case-insensitive filesystem"
            )


def _append_config_block(
    config_path: Path, alias: str, api_id: int, api_hash: str
) -> None:
    """Write a new `[accounts.<alias>]` block.

    The session name must not collide case-insensitively with an existing
    account: `load_config` rejects such a config outright, so writing one
    would leave the user with a config file no command can read.
    """
    _reject_colliding_alias(config_path, alias)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.touch(mode=0o600)
    with config_path.open("a") as config_file:
        config_file.write(
            f"\n[accounts.{alias}]\napi_id = {api_id}\n"
            f"api_hash = {json.dumps(api_hash)}\nsession = {json.dumps(alias)}\n"
        )
    config_path.chmod(0o600)
