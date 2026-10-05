import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tgcli import atomic, safety, session
from tgcli.config import Config, default_config_path, load_config, validate_role_name
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli.output import note
from tgcli.session import state_dir

DEFAULT_IMPORT_ALIASES = ("main", "recklessou", "teamsyncsage")


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


def _remove_config_block(config_path: Path, alias: str) -> None:
    """Drop `[accounts.<alias>]` while preserving unrelated text and comments."""
    text = config_path.read_text()
    header = f"[accounts.{alias}]"
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped == header
            if skipping:
                continue
        if skipping:
            continue
        kept.append(line)
    atomic.replace_text(config_path, "".join(kept))


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
    path = session.session_path(config.accounts[alias])
    bak = Path(str(path) + ".bak")
    session.ensure_state_dir("sessions")
    with session.session_file_lock(path, busy_error=PolicyError):
        session_existed = path.is_file()
        backup_existed = bak.is_file()

        def _status(existed: bool) -> str:
            if keep_session:
                return "kept" if existed else "absent"
            return "deleted" if existed else "absent"

        session_status = _status(session_existed)
        backup_status = _status(backup_existed)

        safety.append_audit(
            "accounts-remove",
            alias,
            {
                "session": session_status,
                "backup": backup_status,
                "keep_session": keep_session,
            },
        )

        config_path = default_config_path()
        _remove_config_block(config_path, alias)
        if not keep_session:
            path.unlink(missing_ok=True)
            bak.unlink(missing_ok=True)

        return {
            "alias": alias,
            "config": "removed",
            "session": session_status,
            "backup": backup_status,
        }


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


def _source_dir(source_root: Path, alias: str) -> Path:
    suffix = "" if alias == "main" else f"-{alias}"
    return source_root / f".telegram-mcp{suffix}"


def _read_credentials(env_path: Path) -> tuple[int, str]:
    if not env_path.exists():
        raise ConfigError(f"credentials file not found: {env_path}")
    values: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    try:
        return int(values["TELEGRAM_API_ID"]), values["TELEGRAM_API_HASH"]
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"unparseable credentials file: {env_path}") from exc


def _backup_sqlite(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


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


def _copy_session(source_path: Path, destination_path: Path) -> None:
    session.ensure_state_dir("sessions")
    with session.session_file_lock(destination_path):
        _backup_sqlite(source_path, destination_path)
        session.restrict_file(destination_path)


def import_accounts(aliases: list[str] | None, source_root: Path, force: bool) -> dict:
    requested = aliases or list(DEFAULT_IMPORT_ALIASES)
    explicit = aliases is not None
    config_path = default_config_path()
    config = load_config(config_path) if config_path.exists() else Config(None, {})
    imported = []
    for alias in requested:
        source_dir = _source_dir(source_root, alias)
        source_path = source_dir / "session.session"
        if not source_path.exists():
            if explicit:
                raise NotFoundError(
                    f"no old-stack session for {alias!r} at {source_dir}"
                )
            note(f"warning: no old-stack session for {alias!r} at {source_dir}")
            imported.append(
                {
                    "alias": alias,
                    "session": str(state_dir() / "sessions" / f"{alias}.session"),
                    "status": "source_missing",
                    "config": "unchanged",
                }
            )
            continue

        destination_path = state_dir() / "sessions" / f"{alias}.session"
        config_status = "unchanged"
        if alias not in config.accounts:
            api_id, api_hash = _read_credentials(source_dir / "launchd.env")
            _append_config_block(config_path, alias, api_id, api_hash)
            config_status = "added"

        if destination_path.exists() and not force:
            status = "skipped_existing"
        else:
            _copy_session(source_path, destination_path)
            status = "imported"
        imported.append(
            {
                "alias": alias,
                "session": str(destination_path),
                "status": status,
                "config": config_status,
            }
        )
    return {"imported": imported}


def import_rows(data: dict) -> list[tuple]:
    return [
        (entry["alias"], entry["status"], entry["config"]) for entry in data["imported"]
    ]
