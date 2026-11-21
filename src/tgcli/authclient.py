"""Telethon client for a session that is not yet authorized.

`session.client()` refuses unauthorized sessions; login starts from that
state, so it needs its own lock + connect/disconnect seam.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from telethon import errors as telethon_errors, functions

from tgcli.config import Account
from tgcli.errors import RateLimitError
from tgcli.session import build_client, session_file_lock, session_path
from tgcli.state_paths import create_private, ensure_state_dir, restrict_file, state_dir
from tgcli.tg_errors import FLOOD_ERRORS


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
        ensure_state_dir(*relative.parts)
    with session_file_lock(path):
        create_private(path)
        tg = build_client(path, api_id, api_hash)
        restrict_file(path)
        try:
            try:
                await tg.connect()
                yield tg
            except FLOOD_ERRORS as exc:
                raise RateLimitError(
                    f"FLOOD_WAIT; retry after {exc.seconds}s",
                    retry_after=exc.seconds,
                ) from exc
        finally:
            if tg.is_connected():
                await tg.disconnect()  # type: ignore[func-returns-value]


async def probe_authorized(account: Account, role: str | None = None) -> bool:
    """Return whether the account's existing session is still authorized.

    Authorization errors map to False; transient RPC errors remain visible.
    """
    path = session_path(account, role)
    try:
        async with unauthorized_client(path, account.api_id, account.api_hash) as tg:
            await tg(functions.updates.GetStateRequest())
            return True
    except telethon_errors.UnauthorizedError:
        return False
