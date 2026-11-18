"""tgcli's TelegramClient: resolves a known @username without ResolveUsername.

Telethon's ``get_entity("@name")`` always sends contacts.ResolveUsername,
which Telegram caps at about 200 a day per account and then answers with a
FloodWait of up to a day. The session already maps every username it has
seen to a peer, so a cached ``@name`` is fetched by peer (users.GetUsers or
channels.GetChannels) instead. The fresh entity is used only while Telegram
still reports that username on it; a username that moved to someone else, or
a peer that refuses us, falls through to ResolveUsername as before.
"""

from __future__ import annotations

from typing import Any

from telethon import TelegramClient, utils
from telethon.tl import types

from tgcli.tg_errors import unless_refused


class Client(TelegramClient):
    async def get_entity(self, entity):
        if isinstance(entity, str):
            cached = await self._cached_username(entity)
            if cached is not None:
                return cached
        return await super().get_entity(entity)

    async def _cached_username(self, ref: str):
        username, is_invite = utils.parse_username(ref)
        if not username or is_invite:
            return None
        # Every concrete Telethon session implements this; the base types it
        # as NotImplementedError.
        session: Any = self.session
        row = session.get_entity_rows_by_username(username)
        if not row:
            return None
        marked_id, access_hash = row
        real_id, kind = utils.resolve_id(marked_id)
        if kind is types.PeerUser:
            peer = types.InputPeerUser(real_id, access_hash)
        elif kind is types.PeerChannel:
            peer = types.InputPeerChannel(real_id, access_hash)
        else:
            return None
        fresh, _ = await unless_refused(super().get_entity(peer))
        if fresh is None or username not in _usernames(fresh):
            return None
        return fresh


def _usernames(entity) -> set[str]:
    names = {getattr(entity, "username", None)}
    names.update(item.username for item in getattr(entity, "usernames", None) or ())
    return {name.lower() for name in names if name}
