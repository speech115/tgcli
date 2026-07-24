import fcntl
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tgcli import safety
from tgcli.config import Config, default_config_path, load_config
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


def _account_session_path(config: Config, alias: str) -> Path:
    if alias not in config.accounts:
        raise NotFoundError(f"unknown account alias: {alias!r}")
    return state_dir() / "sessions" / f"{config.accounts[alias].session}.session"


def _lock_held(session_file: Path) -> bool:
    """Return True if another process holds the session lock.

    Opens the lock path, tries LOCK_EX|LOCK_NB, and releases immediately on
    success. Never waits and never leaves the lock held.
    """
    lock_path = session_file.with_suffix(".lock")
    try:
        handle = lock_path.open("w")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        handle.close()


def show_account(config: Config, alias: str) -> dict:
    path = _account_session_path(config, alias)
    bak = Path(str(path) + ".bak")
    exists = path.is_file()
    bytes_count: int | None = None
    modified: str | None = None
    if exists:
        stat = path.stat()
        bytes_count = stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    return {
        "alias": alias,
        "in_config": True,
        "session": str(path),
        "exists": exists,
        "bytes": bytes_count,
        "modified": modified,
        "locked": _lock_held(path),
        "backup": str(bak) if bak.is_file() else None,
        "authorized": None,
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
    new_text = "".join(kept)
    directory = config_path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, config_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.chmod(config_path, 0o600)


def remove_account(
    config: Config,
    alias: str,
    *,
    confirm: bool,
    keep_session: bool,
) -> dict:
    if alias not in config.accounts:
        raise NotFoundError(f"unknown account alias: {alias!r}")
    if not confirm:
        note(f"refusing to remove account {alias!r}; re-run with --confirm")
        raise PolicyError(f"refusing to remove account {alias!r} without --confirm")
    if config.default_account == alias:
        raise PolicyError(
            f"refusing to remove default_account {alias!r}; "
            "edit default_account in config first"
        )
    path = state_dir() / "sessions" / f"{config.accounts[alias].session}.session"
    bak = Path(str(path) + ".bak")
    if _lock_held(path):
        raise PolicyError(
            f"session {path.stem!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        )

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


def remove_rows(data: dict) -> list[tuple]:
    return [(data["alias"], data["config"], data["session"], data["backup"])]


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


def _append_config_block(
    config_path: Path, alias: str, api_id: int, api_hash: str
) -> None:
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
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination_path.with_suffix(".lock")
    lock = lock_path.open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigError(
                f"session {destination_path.stem!r} is busy "
                "(another tg process is using it); retry in a few seconds"
            ) from exc
        _backup_sqlite(source_path, destination_path)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


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
