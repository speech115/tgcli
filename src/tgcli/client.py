"""tgcli's TelegramClient: resolves a known @username without ResolveUsername.

Telethon's ``get_entity("@name")`` always sends contacts.ResolveUsername,
which Telegram caps at about 200 a day per account and then answers with a
FloodWait of up to a day. The session already maps every username it has
seen to a peer, so a cached ``@name`` is fetched by peer (users.GetUsers or
channels.GetChannels) instead. The fresh entity is used only while Telegram
still reports that username on it; a username that moved to someone else, or
a peer that refuses us, falls through to ResolveUsername as before.

A display name is tried against every session row that carries it, not only
the first, and a peer that refuses us is skipped. ``get_input_entity`` takes
the same path, so a commit targets the peer its preview showed.

It also never logs in Telethon's live-updates machinery. tgcli handles no
pushed updates (`archive sync` asks for its own difference), yet a stock
client spends updates.GetState and updates.GetDifference on every connect and
then chases update gaps with more GetDifference calls for as long as it runs.
Built with `receive_updates=False`, it asks Telegram for no pushes, skips
that login and drops the updates that ride on RPC results. The GetUsers that
proves the session is authorized also answers the command's own `get_me()`.
"""

from __future__ import annotations

from typing import Any, cast

from telethon import TelegramClient, utils
from telethon.sessions import SQLiteSession
from telethon.tl import types

from tgcli.tg_errors import unless_refused


class Client(TelegramClient):
    _cached_me: types.User | None = None

    async def connect(self) -> None:
        self._cached_me = None
        await super().connect()

    async def get_me(self, input_peer: bool = False):
        if self._cached_me is None or input_peer:
            me = await super().get_me(input_peer=input_peer)
            if not input_peer and isinstance(me, types.User):
                self._cached_me = me
            return me
        return self._cached_me

    async def _on_login(self, user):
        if not self._no_updates:
            return await super()._on_login(user)
        self._mb_entity_cache.set_self_user(user.id, user.bot, user.access_hash)
        self._authorized = True
        return user

    async def _update_loop(self) -> None:
        if not self._no_updates:
            return await super()._update_loop()
        while True:
            await self._updates_queue.get()

    async def get_entity(self, entity):
        if isinstance(entity, str):
            cached = await self._cached_username(entity)
            if cached is not None:
                return cached
            if _is_display_name(entity):
                return await self._by_display_name(entity)
        return await super().get_entity(entity)

    async def get_input_entity(self, peer):
        if isinstance(peer, str) and _is_display_name(peer):
            entity = await self._by_display_name(peer)
            return cast(types.TypeInputPeer, utils.get_input_peer(entity))
        return await super().get_input_entity(peer)

    async def _by_display_name(self, name: str):
        for marked_id, access_hash in _rows_named(self.session, name):
            real_id, kind = utils.resolve_id(marked_id)
            if kind is types.PeerUser:
                peer = types.InputPeerUser(real_id, access_hash)
            elif kind is types.PeerChannel:
                peer = types.InputPeerChannel(real_id, access_hash)
            else:
                peer = types.InputPeerChat(real_id)
            fresh, _ = await unless_refused(super().get_entity(peer))
            if fresh is not None:
                return fresh
        raise ValueError(f'Cannot find any entity corresponding to "{name}"')

    async def _cached_username(self, ref: str):
        username, is_invite = utils.parse_username(ref)
        if not username or is_invite:
            return None
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


def _is_display_name(ref: str) -> bool:
    if utils.parse_phone(ref) or ref.lower() in ("me", "self"):
        return False
    username, is_invite = utils.parse_username(ref)
    return not username and not is_invite


def _rows_named(session: Any, name: str) -> list[tuple[int, int]]:
    if isinstance(session, SQLiteSession):
        cursor = session._cursor()
        try:
            return cursor.execute(
                "select id, hash from entities where name = ?", (name,)
            ).fetchall()
        finally:
            cursor.close()
    row = session.get_entity_rows_by_name(name)
    return [row] if row else []


def _usernames(entity) -> set[str]:
    names = {getattr(entity, "username", None)}
    names.update(item.username for item in getattr(entity, "usernames", None) or ())
    return {name.lower() for name in names if name}
