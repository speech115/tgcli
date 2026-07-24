"""Telethon client for a session that is not yet authorized (ADR-0042).

`session.client()` refuses unauthorized sessions; login starts from that
state, so it needs its own lock + connect/disconnect seam.
"""

from __future__ import annotations

import fcntl
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient
from telethon import errors as telethon_errors

from tgcli.config import Account
from tgcli.errors import ConfigError
from tgcli.session import session_path


@asynccontextmanager
async def unauthorized_client(path: Path, api_id: int, api_hash: str):
    """Connect a Telethon client without checking authorization.

    Takes the `.lock` beside `path` exactly as `session.client()` does.
    Always disconnects so SQLite is committed and closed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ConfigError(
            f"session {path.stem!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        ) from None
    tg = TelegramClient(str(path), api_id, api_hash)
    try:
        await tg.connect()
        yield tg
    finally:
        # Login may disconnect before promote so SQLite releases the staged
        # file; a second disconnect would recreate an empty DB at the old path
        # and raise (no entities table) after the session was moved.
        if tg.is_connected():
            await tg.disconnect()  # type: ignore[func-returns-value]
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


async def probe_authorized(account: Account) -> bool:
    """Return whether the account's existing session is still authorized.

    `SessionRevokedError` maps to False rather than raising.
    """
    path = session_path(account)
    try:
        async with unauthorized_client(path, account.api_id, account.api_hash) as tg:
            return bool(await tg.is_user_authorized())
    except telethon_errors.SessionRevokedError:
        return False
