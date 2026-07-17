# tests/test_clone_attribution.py
"""Direct unit tests for clone author attribution."""
import asyncio
from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import attribution


def test_prefixed_without_author_keeps_text_and_entities():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed("жирный", [entity], None)
    assert text == "жирный"
    assert entities == [entity]


def test_prefixed_shifts_entity_offsets_by_utf16_prefix():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed("текст", [entity], "Иван")
    assert text == "Иван: текст"
    assert entities[0].offset == 6  # len("Иван: ") in UTF-16 units
    assert entities[0] is not entity  # original must not be mutated


def test_prefixed_handles_surrogate_pair_author():
    text, entities = attribution.prefixed("hi", None, "😀")
    assert text == "😀: hi"
    assert entities is None


def test_author_name_uses_me_for_own_messages():
    me = types.User(id=1, first_name="Me")
    message = SimpleNamespace(from_id=types.PeerUser(user_id=1),
                              sender_id=1, out=True)
    name = asyncio.run(attribution.author_name(
        None, SimpleNamespace(id=2), message, me, {}, None))
    assert name == "Me"


def test_author_name_caches_entity_lookups():
    calls = []

    class Client:
        async def get_entity(self, peer):
            calls.append(peer)
            return SimpleNamespace(title="Chan")

    async def cooldown(awaitable):
        return await awaitable

    cache = {}
    me = SimpleNamespace(id=1)
    message = SimpleNamespace(from_id=types.PeerChannel(channel_id=9),
                              sender_id=9, out=False)
    source = SimpleNamespace(id=2)
    first = asyncio.run(attribution.author_name(
        Client(), source, message, me, cache, cooldown))
    second = asyncio.run(attribution.author_name(
        Client(), source, message, me, cache, cooldown))
    assert first == second == "Chan"
    assert len(calls) == 1


def test_author_name_falls_back_to_id_for_unresolvable_peer():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(awaitable):
        return await awaitable

    me = SimpleNamespace(id=1)
    message = SimpleNamespace(from_id=types.PeerUser(user_id=9),
                              sender_id=9, out=False)
    name = asyncio.run(attribution.author_name(
        Client(), SimpleNamespace(id=2), message, me, {}, cooldown))
    assert name == "id 9"
