"""Session files, per-account locks, Telethon client lifecycle (ADR-0004)."""

import fcntl
import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient
from telethon import errors as telethon_errors

from tgcli import __version__
from tgcli.config import Account
from tgcli.errors import ConfigError


def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def session_path(account: Account) -> Path:
    return state_dir() / "sessions" / f"{account.session}.session"


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


def _make_client(
    path: Path, account: Account, *, mutation_safe: bool = False
) -> TelegramClient:
    device_model, system_version, app_version = client_identity()
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
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
    )


@asynccontextmanager
async def client(account: Account, *, mutation_safe: bool = False):
    path = session_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ConfigError(
            f"session {account.session!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        ) from None
    tg = _make_client(path, account, mutation_safe=mutation_safe)
    try:
        await tg.connect()
        if not await tg.is_user_authorized():
            raise ConfigError(
                f"session {account.session!r} is not authorized; "
                "run: tg accounts login <alias> "
                "(or tg accounts import for an old-stack session)"
            )
        yield tg
    except telethon_errors.SessionRevokedError as exc:
        raise ConfigError(
            f"session {account.session!r} needs reauthentication; authorize it again"
        ) from exc
    finally:
        await tg.disconnect()  # type: ignore  # Telethon stub: Coroutine | None
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
