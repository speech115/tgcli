# tests/test_clone_attribution.py
"""Direct unit tests for clone author attribution."""

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import attribution


def test_destination_title_prefixes_source_name():
    assert (
        attribution.destination_title("Джарвис ⚔ ИИздец") == "[Clone] Джарвис ⚔ ИИздец"
    )


def test_prefixed_without_author_keeps_text_and_entities():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed("жирный", [entity], None)
    assert text == "жирный"
    assert entities == [entity]


def test_prefixed_shifts_entity_offsets_by_utf16_prefix():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed(
        "текст", [entity], attribution.Author(text="Иван")
    )
    assert text == "Иван: \n\nтекст"
    assert entities[0].offset == 8  # len("Иван: \n\n") in UTF-16 units
    assert entities[0] is not entity  # original must not be mutated


def test_prefixed_handles_surrogate_pair_author():
    text, entities = attribution.prefixed("hi", None, attribution.Author(text="😀"))
    assert text == "😀: \n\nhi"
    assert entities is None


def test_prefixed_with_username_author_adds_no_mention_entity():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="Ivan (@ivan)")
    )
    assert text == "Ivan (@ivan): \n\nhi"
    assert entities is None


def test_prefixed_mentions_author_without_username():
    text, entities = attribution.prefixed(
        "текст", None, attribution.Author(text="Иван", mention_user_id=7)
    )
    assert text == "Иван: \n\nтекст"
    assert entities == [types.MessageEntityMentionName(offset=0, length=4, user_id=7)]


def test_prefixed_mention_length_counts_utf16_units():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="😀 Ann", mention_user_id=7)
    )
    assert text == "😀 Ann: \n\nhi"
    assert entities[0].length == 6  # surrogate pair counts as 2


def test_prefixed_mention_coexists_with_shifted_entities():
    bold = types.MessageEntityBold(offset=0, length=2)
    text, entities = attribution.prefixed(
        "hi", [bold], attribution.Author(text="Ann", mention_user_id=7)
    )
    assert text == "Ann: \n\nhi"
    assert entities == [
        types.MessageEntityMentionName(offset=0, length=3, user_id=7),
        types.MessageEntityBold(offset=7, length=2),
    ]
    assert bold.offset == 0  # original must not be mutated


def test_prefixed_lead_in_renders_forward_line_and_shifts_mention():
    """ADR-0050: Author.lead builds ``Переслано от <name>``; mention covers name only."""
    text, entities = attribution.prefixed(
        "тело",
        None,
        attribution.Author(text="Имя", mention_user_id=42, lead="Переслано от "),
    )
    assert text == "Переслано от Имя\n\nтело"
    assert entities == [
        types.MessageEntityMentionName(
            offset=attribution.utf16_len("Переслано от "),
            length=attribution.utf16_len("Имя"),
            user_id=42,
        )
    ]


def test_prefixed_empty_lead_keeps_speaker_label_byte_for_byte():
    """Existing speaker-label callers (lead=\"\") keep ``{text}: \\n\\n``."""
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="Ann", mention_user_id=7, lead="")
    )
    assert text == "Ann: \n\nhi"
    assert entities == [types.MessageEntityMentionName(offset=0, length=3, user_id=7)]


def test_prefixed_lead_in_with_non_bmp_shifts_body_by_utf16_len():
    bold = types.MessageEntityBold(offset=0, length=2)
    lead = "Переслано от 😀 "
    text, entities = attribution.prefixed(
        "hi", [bold], attribution.Author(text="Ann", lead=lead)
    )
    assert text == f"{lead}Ann\n\nhi"
    assert entities[0].offset == attribution.utf16_len(f"{lead}Ann\n\n")
    assert bold.offset == 0


def test_author_of_uses_me_for_own_messages():
    me = types.User(id=1, first_name="Me")
    message = SimpleNamespace(from_id=types.PeerUser(user_id=1), sender_id=1, out=True)
    author = asyncio.run(
        attribution.author_of(None, SimpleNamespace(id=2), message, me, {}, None)
    )
    assert author == attribution.Author(text="Me", mention_user_id=1)


def test_author_of_caches_entity_lookups():
    calls = []

    class Client:
        async def get_entity(self, peer):
            calls.append(peer)
            return SimpleNamespace(title="Chan")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    cache = {}
    me = SimpleNamespace(id=1)
    message = SimpleNamespace(
        from_id=types.PeerChannel(channel_id=9), sender_id=9, out=False
    )
    source = SimpleNamespace(id=2)
    first = asyncio.run(
        attribution.author_of(Client(), source, message, me, cache, cooldown)
    )
    second = asyncio.run(
        attribution.author_of(Client(), source, message, me, cache, cooldown)
    )
    assert first == second == attribution.Author(text="Chan")
    assert len(calls) == 1


def test_author_of_prefers_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username="ivan")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Ivan (@ivan)")


def test_author_of_prefers_active_username_from_usernames():
    class Client:
        async def get_entity(self, peer):
            return types.User(
                id=9,
                first_name="Ivan",
                username=None,
                usernames=[
                    types.Username(username="old", active=False),
                    types.Username(username="ivan", active=True),
                ],
            )

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Ivan (@ivan)")


def test_author_of_mentions_user_without_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Ivan", mention_user_id=9)


def test_author_of_does_not_mention_non_user_senders():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(id=9, title="Chan", username=None, usernames=[])

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(
                from_id=types.PeerChannel(channel_id=9), sender_id=9, out=False
            ),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Chan")


def test_author_of_falls_back_to_id_for_unresolvable_peer():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="id 9")


def test_author_of_falls_back_to_id_when_get_entity_raises_rpc_error():
    """Live `[икона]` 54 shape: a private channel refuses `get_entity` with
    an `RPCError` subclass, not `ValueError` — must fall back, not raise."""

    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.ChannelPrivateError(request=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="id 9")


def test_author_of_propagates_flood_wait():
    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.FloodWaitError(request=None, capture=30)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    with pytest.raises(telethon_errors.FloodWaitError):
        asyncio.run(
            attribution.author_of(
                Client(),
                SimpleNamespace(id=2),
                SimpleNamespace(
                    from_id=types.PeerUser(user_id=9), sender_id=9, out=False
                ),
                SimpleNamespace(id=1),
                {},
                cooldown,
            )
        )


def test_author_of_uses_post_author_when_sender_is_absent():
    author = asyncio.run(
        attribution.author_of(
            None,
            SimpleNamespace(id=2, __class__=SimpleNamespace),
            SimpleNamespace(
                from_id=None, sender_id=None, out=False, post_author="Editor"
            ),
            SimpleNamespace(id=1),
            {},
            None,
        )
    )
    assert author == attribution.Author(text="Editor")


def test_author_of_falls_back_to_id_unknown_without_sender_or_signature():
    author = asyncio.run(
        attribution.author_of(
            None,
            SimpleNamespace(id=2, __class__=SimpleNamespace),
            SimpleNamespace(from_id=None, sender_id=None, out=False, post_author=None),
            SimpleNamespace(id=1),
            {},
            None,
        )
    )
    assert author == attribution.Author(text="id unknown")


ANONYMOUS_GROUP = SimpleNamespace(id=3767845640, title="Режим", megagroup=True)


def _anonymous_comment(post_author=None):
    """Issue #81 live shape: anonymous admin posting as the discussion group."""
    return SimpleNamespace(
        from_id=None, sender_id=None, out=False, post_author=post_author
    )


def test_author_of_names_the_group_for_an_anonymous_as_group_comment():
    """An anonymous admin posting as the group ships no `from_id` and no
    `post_author`; every client labels it with the group's own title (#81)."""
    author = asyncio.run(
        attribution.author_of(
            None,
            ANONYMOUS_GROUP,
            _anonymous_comment(),
            SimpleNamespace(id=1),
            {},
            None,
        )
    )
    assert author == attribution.Author(text="Режим")
    assert attribution.prefixed("Профи", None, author)[0] == "Режим: \n\nПрофи"


def test_author_of_prefers_post_author_over_the_group_title():
    author = asyncio.run(
        attribution.author_of(
            None,
            ANONYMOUS_GROUP,
            _anonymous_comment(post_author="Editor"),
            SimpleNamespace(id=1),
            {},
            None,
        )
    )
    assert author == attribution.Author(text="Editor")


def test_author_of_keeps_the_channel_for_a_channel_as_author_comment():
    """A comment sent as the source broadcast still resolves that channel."""

    class Client:
        async def get_entity(self, peer):
            assert peer == types.PeerChannel(channel_id=3740847993)
            return SimpleNamespace(id=3740847993, title="Пылесос")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            ANONYMOUS_GROUP,
            SimpleNamespace(
                from_id=types.PeerChannel(channel_id=3740847993),
                sender_id=3740847993,
                out=False,
                post_author=None,
            ),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Пылесос")


def test_author_of_keeps_the_user_ladder_for_an_ordinary_comment():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=828831254, first_name="Филипп", last_name="Козлов")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.author_of(
            Client(),
            ANONYMOUS_GROUP,
            SimpleNamespace(
                from_id=types.PeerUser(user_id=828831254),
                sender_id=828831254,
                out=False,
                post_author=None,
            ),
            SimpleNamespace(id=1),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Филипп Козлов", mention_user_id=828831254)


def test_author_of_falls_back_to_id_unknown_without_peer_or_ambient_chat():
    author = asyncio.run(
        attribution.author_of(
            None, None, _anonymous_comment(), SimpleNamespace(id=1), {}, None
        )
    )
    assert author == attribution.Author(text="id unknown")


FORWARD_LEAD = "Переслано от "


def _fwd_message(**fwd_fields):
    return SimpleNamespace(
        fwd_from=types.MessageFwdHeader(date=None, **fwd_fields),
        message="body",
        entities=None,
    )


def test_forwarded_author_of_resolves_peer_user_like_identify():
    class Client:
        async def get_entity(self, peer):
            assert peer == types.PeerUser(user_id=9)
            return types.User(id=9, first_name="Ivan", username="ivan")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(), _fwd_message(from_id=types.PeerUser(user_id=9)), {}, cooldown
        )
    )
    assert author == attribution.Author(text="Ivan (@ivan)", lead=FORWARD_LEAD)


def test_forwarded_author_of_mentions_user_without_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(), _fwd_message(from_id=types.PeerUser(user_id=9)), {}, cooldown
        )
    )
    assert author == attribution.Author(
        text="Ivan", mention_user_id=9, lead=FORWARD_LEAD
    )


def test_forwarded_author_of_uses_channel_title_without_mention():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(id=9, title="News", username=None, usernames=[])

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(),
            _fwd_message(from_id=types.PeerChannel(channel_id=9)),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="News", lead=FORWARD_LEAD)
    text, entities = attribution.prefixed("body", None, author)
    assert text == "Переслано от News\n\nbody"
    assert entities is None


def test_forwarded_author_of_from_name_is_verbatim_without_mention():
    """Privacy rung: hidden account → from_name only; never re-link."""
    author = asyncio.run(
        attribution.forwarded_author_of(
            None, _fwd_message(from_name="Кто-то"), {}, None
        )
    )
    assert author == attribution.Author(text="Кто-то", lead=FORWARD_LEAD)
    assert author.mention_user_id is None
    text, entities = attribution.prefixed("body", None, author)
    assert text == "Переслано от Кто-то\n\nbody"
    assert entities is None
    assert not any(
        isinstance(entity, types.MessageEntityMentionName)
        for entity in (entities or ())
    )


def test_forwarded_author_of_uses_post_author_signature():
    author = asyncio.run(
        attribution.forwarded_author_of(
            None, _fwd_message(post_author="Редакция"), {}, None
        )
    )
    assert author == attribution.Author(text="Редакция", lead=FORWARD_LEAD)


def test_forwarded_author_of_bare_word_when_nothing_resolvable():
    author = asyncio.run(
        attribution.forwarded_author_of(None, _fwd_message(), {}, None)
    )
    assert author == attribution.Author(text="", lead="Переслано")
    text, entities = attribution.prefixed("body", None, author)
    assert text == "Переслано\n\nbody"
    assert entities is None


def test_forwarded_author_of_falls_back_when_get_entity_raises_value_error():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(),
            _fwd_message(from_id=types.PeerUser(user_id=9), from_name="Кто-то"),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Кто-то", lead=FORWARD_LEAD)


def test_forwarded_author_of_falls_back_to_from_name_when_get_entity_raises_rpc_error():
    """Live `[икона]` 54 shape: `fwd_from.from_id = PeerChannel(...)` names a
    private channel this account cannot access — `ChannelPrivateError` is an
    `RPCError`, not `ValueError`, and must still fall through the ladder."""

    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.ChannelPrivateError(request=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(),
            _fwd_message(
                from_id=types.PeerChannel(channel_id=1987061209), from_name="Кто-то"
            ),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="Кто-то", lead=FORWARD_LEAD)


def test_forwarded_author_of_falls_back_to_bare_word_when_get_entity_raises_rpc_error():
    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.ChannelPrivateError(request=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(),
            _fwd_message(from_id=types.PeerChannel(channel_id=1987061209)),
            {},
            cooldown,
        )
    )
    assert author == attribution.Author(text="", lead="Переслано")


def test_forwarded_author_of_caches_rpc_error_refusal():
    calls = []

    class Client:
        async def get_entity(self, peer):
            calls.append(peer)
            raise telethon_errors.ChannelPrivateError(request=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    cache = {}
    client = Client()
    msg = _fwd_message(from_id=types.PeerChannel(channel_id=1987061209))
    first = asyncio.run(attribution.forwarded_author_of(client, msg, cache, cooldown))
    second = asyncio.run(attribution.forwarded_author_of(client, msg, cache, cooldown))
    assert first == second == attribution.Author(text="", lead="Переслано")
    assert len(calls) == 1


def test_forwarded_author_of_reuses_author_cache():
    calls = []

    class Client:
        async def get_entity(self, peer):
            calls.append(peer)
            return types.User(id=9, first_name="Ivan", username=None)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    cache = {}
    msg = _fwd_message(from_id=types.PeerUser(user_id=9))
    first = asyncio.run(attribution.forwarded_author_of(Client(), msg, cache, cooldown))
    second = asyncio.run(
        attribution.forwarded_author_of(Client(), msg, cache, cooldown)
    )
    assert (
        first
        == second
        == attribution.Author(text="Ivan", mention_user_id=9, lead=FORWARD_LEAD)
    )
    assert len(calls) == 1


def test_forwarded_author_of_propagates_flood_wait():
    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.FloodWaitError(request=None, capture=30)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    with pytest.raises(telethon_errors.FloodWaitError):
        asyncio.run(
            attribution.forwarded_author_of(
                Client(),
                _fwd_message(from_id=types.PeerUser(user_id=9)),
                {},
                cooldown,
            )
        )
