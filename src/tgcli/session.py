"""Session files, per-account locks, Telethon client lifecycle (ADR-0004/0062)."""

import fcntl
import os
import platform
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient, errors as telethon_errors

from tgcli import __version__
from tgcli.config import Account, validate_role_name
from tgcli.errors import ConfigError


def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def _repair_mode(path: Path, mode: int) -> None:
    """Fail-open chmod: permissions are protection, not a new failure mode."""
    try:
        if path.stat().st_mode & 0o777 != mode:
            os.chmod(path, mode)
    except OSError:
        pass


def ensure_state_dir(*parts: str) -> Path:
    """Create and mode-repair the state root and each named level under it.

    Everything under the state root is tgcli's property and guards account
    secrets (a `.session` file is full access to a Telegram account), so
    every level is forced to 0700. `mkdir(mode=...)` alone is not enough:
    the mode applies only to the leaf, is masked by umask, and
    `exist_ok=True` keeps a wrong mode on an existing directory.
    Directories above the state root are never touched — a custom
    TGCLI_STATE_DIR's parents are not ours.
    """
    path = state_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _repair_mode(path, 0o700)
    for part in parts:
        path = path / part
        path.mkdir(mode=0o700, exist_ok=True)
        _repair_mode(path, 0o700)
    return path


def restrict_file(path: Path) -> None:
    """Force a tgcli state file to 0600, fail-open (missing file: no-op)."""
    _repair_mode(path, 0o600)


def _role_stem(account: Account, role: str) -> str:
    """``<session>@<role>`` stem, reusing an on-disk casefold match when present."""
    validate_role_name(role)
    wanted = f"{account.session}@{role}".casefold()
    directory = state_dir() / "sessions"
    if directory.is_dir():
        for path in directory.glob("*.session"):
            if path.stem.casefold() == wanted:
                return path.stem
    return f"{account.session}@{role}"


def session_path(account: Account, role: str | None = None) -> Path:
    if role is None:
        stem = account.session
    else:
        stem = _role_stem(account, role)
    return state_dir() / "sessions" / f"{stem}.session"


def list_roles(account: Account) -> list[str]:
    """Role names authorized beside the primary, sorted case-insensitively."""
    directory = state_dir() / "sessions"
    if not directory.is_dir():
        return []
    prefix = f"{account.session}@".casefold()
    roles: list[str] = []
    for path in directory.glob("*.session"):
        stem = path.stem
        if stem.casefold().startswith(prefix) and "@" in stem:
            roles.append(stem.split("@", 1)[1])
    roles.sort(key=str.casefold)
    return roles


def session_label(account: Account, role: str | None = None) -> str:
    if role is None:
        return account.session
    return _role_stem(account, role)


def lock_held(session_file: Path) -> bool | None:
    """Probe whether another process holds this session's lock. Side-effect
    aware: a missing session is never locked and creates no lock file
    (CONTRACT §5.1), because opening the lock path would create it.

    Tries LOCK_EX|LOCK_NB and releases immediately on success — never waits,
    never keeps the lock. Returns None when the probe is impossible (the lock
    path cannot be opened): `accounts show` reports that as not locked, while
    `doctor` treats an unprobeable lock as unhealthy — callers decide.
    """
    if not session_file.is_file():
        return False
    lock_path = session_file.with_suffix(".lock")
    try:
        handle = lock_path.open("w")
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        handle.close()


def client_identity() -> tuple[str, str, str]:
    """Stable Telegram Devices identity for every tgcli connection."""
    return "tgcli", platform.system(), __version__


def session_user_id(session_file: Path) -> int | None:
    """The logged-in user id recorded in a Telethon session file, or None.

    Telethon persists the self-user as the entity with ``id=0`` whose
    ``access_hash`` is the user id (its "hack to not need to change the
    session files", `telegrambaseclient.py`). Reading it directly lets
    `doctor` report the governor's cooldowns without connecting — the one
    command that must work precisely when everything else refuses. Opened
    read-only so a locked session file degrades to None, not a write error.
    """
    try:
        connection = sqlite3.connect(f"file:{session_file}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return None
    try:
        row = connection.execute("SELECT hash FROM entities WHERE id = 0").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    value = row[0]
    return value if isinstance(value, int) else None


def _make_client(
    path: Path, account: Account, *, mutation_safe: bool = False
) -> TelegramClient:
    device_model, system_version, app_version = client_identity()
    # ADR-0072 decision 2: `flood_sleep_threshold=0` on *every* client, not
    # just the mutation-safe ones. Telethon's own sleeping is what made the
    # incident invisible — it engages only above `limit > 3000` and silently
    # absorbs shorter waits, so the governor could never see the flood it is
    # supposed to record. Retries stay at Telethon's default for reads.
    # Imported here, not at module scope: the governor's ledger needs this
    # module's state-dir helpers, so a top-level import would be circular.
    from tgcli.governor.seam import verify_seam

    verify_seam()
    if mutation_safe:
        return TelegramClient(
            str(path),
            account.api_id,
            account.api_hash,
            request_retries=0,
            flood_sleep_threshold=0,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
        )
    return TelegramClient(
        str(path),
        account.api_id,
        account.api_hash,
        flood_sleep_threshold=0,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
    )


@asynccontextmanager
async def client(
    account: Account, *, mutation_safe: bool = False, role: str | None = None
):
    path = session_path(account, role)
    label = session_label(account, role)
    if role is not None and not path.is_file():
        raise ConfigError(
            f"session role {role!r} for account {account.alias!r} is not authorized; "
            f"run: tg accounts login {account.alias} --role {role}"
        )
    ensure_state_dir("sessions")
    lock = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ConfigError(
            f"session {label!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        ) from None
    tg = _make_client(path, account, mutation_safe=mutation_safe)
    # Telethon creates the SQLite session during client construction; tighten it
    # before any network use.
    restrict_file(path)
    from tgcli.governor.gate import install as install_governor
    from tgcli.governor.ledger import Ledger

    governor = Ledger.open()
    install_governor(tg, governor)
    try:
        await tg.connect()
        if not await tg.is_user_authorized():
            if role is not None:
                raise ConfigError(
                    f"session role {role!r} for account {account.alias!r} "
                    "is not authorized; "
                    f"run: tg accounts login {account.alias} --role {role}"
                )
            raise ConfigError(
                f"session {account.session!r} is not authorized; "
                "run: tg accounts login <alias> "
                "(or tg accounts import for an old-stack session)"
            )
        yield tg
    except telethon_errors.SessionRevokedError as exc:
        raise ConfigError(
            f"session {label!r} needs reauthentication; authorize it again"
        ) from exc
    finally:
        await tg.disconnect()  # type: ignore  # Telethon stub: Coroutine | None
        governor.close()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
