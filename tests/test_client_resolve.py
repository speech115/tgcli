"""`Client.get_entity("@name")` reuses the session's username cache.

ResolveUsername is capped at about 200 a day per account; a cached username
costs one GetUsers/GetChannels instead, and only while Telegram still
reports that username on the peer.
"""

from __future__ import annotations

from typing import Any

import pytest
from telethon import errors as telethon_errors, utils
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from tgcli.client import Client


def _user(user_id: int, username: str | None) -> types.User:
    return types.User(id=user_id, access_hash=user_id * 10, username=username)


def _channel(channel_id: int, username: str) -> types.Channel:
    return types.Channel(
        id=channel_id,
        title="C",
        photo=types.ChatPhotoEmpty(),
        date=None,
        access_hash=channel_id * 10,
        username=username,
        broadcast=True,
    )


def _get_user(user_id: int):
    return functions.users.GetUsersRequest(
        id=[types.InputPeerUser(user_id=user_id, access_hash=user_id * 10)]
    )


class RecordingClient(Client):
    """A tgcli Client that never connects: it answers RPCs from tables."""

    def __init__(self, *, entities=(), resolved=None, fail=None):
        super().__init__(MemorySession(), 1, "hash")
        self.requests: list[Any] = []
        self._by_id = {entity.id: entity for entity in entities}
        self._resolved = resolved
        self._fail = fail

    def remember(self, *entities) -> None:
        """What Telethon does with every entity a response carries."""
        tlo: Any = types.messages.Chats(chats=list(entities))
        assert self.session is not None
        self.session.process_entities(tlo)

    def sent(self) -> list[dict]:
        return [request.to_dict() for request in self.requests]

    async def __call__(self, request, *_args, **_kwargs):  # type: ignore[override]
        self.requests.append(request)
        if self._fail is not None:
            raise self._fail
        if isinstance(request, functions.users.GetUsersRequest):
            return [self._by_id[utils.get_peer_id(item)] for item in request.id]
        if isinstance(request, functions.channels.GetChannelsRequest):
            ids = [utils.get_peer_id(item, add_mark=False) for item in request.id]
            return types.messages.Chats(chats=[self._by_id[i] for i in ids])
        if isinstance(request, functions.contacts.GetContactsRequest):
            contacts: Any = types.contacts.Contacts(
                contacts=[], saved_count=0, users=[]
            )
            return contacts
        if isinstance(request, functions.contacts.ResolveUsernameRequest):
            if self._resolved is None:
                raise telethon_errors.UsernameNotOccupiedError(request)
            response: Any = types.contacts.ResolvedPeer(
                peer=types.PeerUser(self._resolved.id),
                chats=[],
                users=[self._resolved],
            )
            return response
        raise AssertionError(f"unexpected {request!r}")


@pytest.mark.asyncio
async def test_a_cached_username_is_fetched_by_peer_not_resolved():
    alice = _user(7, "alice")
    client = RecordingClient(entities=[alice])
    client.remember(alice)

    entity = await client.get_entity("@Alice")

    assert entity.id == 7
    assert client.sent() == [_get_user(7).to_dict()]


@pytest.mark.asyncio
async def test_a_cached_channel_username_is_fetched_by_peer():
    news = _channel(5, "news")
    client = RecordingClient(entities=[news])
    client.remember(news)

    entity = await client.get_entity("https://t.me/news")

    assert entity.id == 5
    assert client.sent() == [
        functions.channels.GetChannelsRequest(
            id=[types.InputPeerChannel(channel_id=5, access_hash=50)]
        ).to_dict()
    ]


@pytest.mark.asyncio
async def test_a_username_that_moved_is_resolved_again():
    client = RecordingClient(
        entities=[_user(7, "alice_old")], resolved=_user(8, "alice")
    )
    client.remember(_user(7, "alice"))

    entity = await client.get_entity("@alice")

    assert entity.id == 8
    assert client.sent() == [
        _get_user(7).to_dict(),
        functions.contacts.ResolveUsernameRequest(username="alice").to_dict(),
    ]


@pytest.mark.asyncio
async def test_an_unknown_username_is_resolved():
    client = RecordingClient(resolved=_user(9, "bob"))

    entity = await client.get_entity("@bob")

    assert entity.id == 9
    assert client.sent() == [
        functions.contacts.ResolveUsernameRequest(username="bob").to_dict()
    ]


@pytest.mark.asyncio
async def test_a_phone_never_matches_a_cached_peer_without_a_username():
    nameless = _user(3, None)
    client = RecordingClient(entities=[nameless])
    client.remember(nameless)

    with pytest.raises(ValueError):
        await client.get_entity("+15550001111")
    assert [request["_"] for request in client.sent()] == ["GetContactsRequest"]
