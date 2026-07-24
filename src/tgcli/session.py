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
                "run: tg accounts import (phase 6) or authorize manually"
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
