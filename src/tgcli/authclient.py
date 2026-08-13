"""Telethon client for a session that is not yet authorized (ADR-0042).

`session.client()` refuses unauthorized sessions; login starts from that
state, so it needs its own lock + connect/disconnect seam.
"""

from __future__ import annotations

import fcntl
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient, errors as telethon_errors

from tgcli.config import Account
from tgcli.errors import ConfigError
from tgcli.session import (
    client_identity,
    ensure_state_dir,
    restrict_file,
    session_path,
    state_dir,
)


@asynccontextmanager
async def unauthorized_client(path: Path, api_id: int, api_hash: str):
    """Connect a Telethon client without checking authorization.

    Takes the `.lock` beside `path` exactly as `session.client()` does.
    Always disconnects so SQLite is committed and closed.
    """
    try:
        relative = path.parent.relative_to(state_dir())
    except ValueError:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        # A state-root parent (sessions/ or logins/) is tgcli's property:
        # create and mode-repair it like every other confirmed site.
        ensure_state_dir(*relative.parts)
    lock = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ConfigError(
            f"session {path.stem!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        ) from None
    device_model, system_version, app_version = client_identity()
    tg = TelegramClient(
        str(path),
        api_id,
        api_hash,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        # ADR-0072 decision 2: never silently sleep floods on login either.
        flood_sleep_threshold=0,
    )
    # Telethon creates the SQLite session during construction; tighten it
    # before any network use.
    restrict_file(path)
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


async def probe_authorized(account: Account, role: str | None = None) -> bool:
    """Return whether the account's existing session is still authorized.

    `SessionRevokedError` maps to False rather than raising.
    """
    path = session_path(account, role)
    try:
        async with unauthorized_client(path, account.api_id, account.api_hash) as tg:
            return bool(await tg.is_user_authorized())
    except telethon_errors.SessionRevokedError:
        return False
