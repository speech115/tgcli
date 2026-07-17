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
    text, entities = attribution.prefixed(
        "текст", [entity], attribution.Author(text="Иван"))
    assert text == "Иван: \n\nтекст"
    assert entities[0].offset == 8  # len("Иван: \n\n") in UTF-16 units
    assert entities[0] is not entity  # original must not be mutated


def test_prefixed_handles_surrogate_pair_author():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="😀"))
    assert text == "😀: \n\nhi"
    assert entities is None


def test_prefixed_with_username_author_adds_no_mention_entity():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="Ivan (@ivan)"))
    assert text == "Ivan (@ivan): \n\nhi"
    assert entities is None


def test_prefixed_mentions_author_without_username():
    text, entities = attribution.prefixed(
        "текст", None, attribution.Author(text="Иван", mention_user_id=7))
    assert text == "Иван: \n\nтекст"
    assert entities == [types.MessageEntityMentionName(
        offset=0, length=4, user_id=7)]


def test_prefixed_mention_length_counts_utf16_units():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="😀 Ann", mention_user_id=7))
    assert text == "😀 Ann: \n\nhi"
    assert entities[0].length == 6  # surrogate pair counts as 2


def test_prefixed_mention_coexists_with_shifted_entities():
    bold = types.MessageEntityBold(offset=0, length=2)
    text, entities = attribution.prefixed(
        "hi", [bold], attribution.Author(text="Ann", mention_user_id=7))
    assert text == "Ann: \n\nhi"
    assert entities == [types.MessageEntityMentionName(offset=0, length=3, user_id=7),
                        types.MessageEntityBold(offset=7, length=2)]
    assert bold.offset == 0  # original must not be mutated


def test_author_of_uses_me_for_own_messages():
    me = types.User(id=1, first_name="Me")
    message = SimpleNamespace(from_id=types.PeerUser(user_id=1),
                              sender_id=1, out=True)
    author = asyncio.run(attribution.author_of(
        None, SimpleNamespace(id=2), message, me, {}, None))
    assert author == attribution.Author(text="Me", mention_user_id=1)


def test_author_of_caches_entity_lookups():
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
    first = asyncio.run(attribution.author_of(
        Client(), source, message, me, cache, cooldown))
    second = asyncio.run(attribution.author_of(
        Client(), source, message, me, cache, cooldown))
    assert first == second == attribution.Author(text="Chan")
    assert len(calls) == 1


def test_author_of_prefers_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username="ivan")

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Ivan (@ivan)")


def test_author_of_prefers_active_username_from_usernames():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username=None, usernames=[
                types.Username(username="old", active=False),
                types.Username(username="ivan", active=True)])

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Ivan (@ivan)")


def test_author_of_mentions_user_without_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username=None)

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Ivan", mention_user_id=9)


def test_author_of_does_not_mention_non_user_senders():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(id=9, title="Chan", username=None, usernames=[])

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerChannel(channel_id=9), sender_id=9,
                        out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Chan")


def test_author_of_falls_back_to_id_for_unresolvable_peer():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="id 9")


def test_author_of_uses_post_author_when_sender_is_absent():
    author = asyncio.run(attribution.author_of(
        None, SimpleNamespace(id=2, __class__=SimpleNamespace),
        SimpleNamespace(from_id=None, sender_id=None, out=False,
                        post_author="Editor"),
        SimpleNamespace(id=1), {}, None))
    assert author == attribution.Author(text="Editor")


def test_author_of_falls_back_to_id_unknown_without_sender_or_signature():
    author = asyncio.run(attribution.author_of(
        None, SimpleNamespace(id=2, __class__=SimpleNamespace),
        SimpleNamespace(from_id=None, sender_id=None, out=False, post_author=None),
        SimpleNamespace(id=1), {}, None))
    assert author == attribution.Author(text="id unknown")
