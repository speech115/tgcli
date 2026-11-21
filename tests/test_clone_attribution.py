"""Direct unit tests for clone author attribution."""

import asyncio
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import attribution


def test_prefixed_shifts_entity_offsets_by_utf16_prefix():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed(
        "текст", [entity], attribution.Author(text="Иван")
    )
    assert text == "Иван: \n\nтекст"
    assert entities[0].offset == 8
    assert entities[0] is not entity


def test_prefixed_mention_length_counts_utf16_units():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="😀 Ann", mention_user_id=7)
    )
    assert text == "😀 Ann: \n\nhi"
    assert entities[0].length == 6


def test_author_of_uses_me_for_own_messages():
    me = types.User(id=1, first_name="Me")
    message = SimpleNamespace(from_id=types.PeerUser(user_id=1), sender_id=1, out=True)
    author = asyncio.run(
        attribution.author_of(None, SimpleNamespace(id=2), message, me, {})
    )
    assert author == attribution.Author(text="Me", mention_user_id=1)


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

    author = asyncio.run(
        attribution.author_of(
            Client(),
            SimpleNamespace(id=2),
            SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
            SimpleNamespace(id=1),
            {},
        )
    )
    assert author == attribution.Author(text="Ivan (@ivan)")


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
            None, ANONYMOUS_GROUP, _anonymous_comment(), SimpleNamespace(id=1), {}
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
        )
    )
    assert author == attribution.Author(text="Editor")


def test_author_of_falls_back_to_id_unknown_without_peer_or_ambient_chat():
    author = asyncio.run(
        attribution.author_of(
            None, None, _anonymous_comment(), SimpleNamespace(id=1), {}
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

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(), _fwd_message(from_id=types.PeerUser(user_id=9)), {}
        )
    )
    assert author == attribution.Author(text="Ivan (@ivan)", lead=FORWARD_LEAD)


def test_forwarded_author_of_uses_post_author_signature():
    author = asyncio.run(
        attribution.forwarded_author_of(None, _fwd_message(post_author="Редакция"), {})
    )
    assert author == attribution.Author(text="Редакция", lead=FORWARD_LEAD)


def test_forwarded_author_of_bare_word_when_nothing_resolvable():
    author = asyncio.run(attribution.forwarded_author_of(None, _fwd_message(), {}))
    assert author == attribution.Author(text="", lead="Переслано")
    text, entities = attribution.prefixed("body", None, author)
    assert text == "Переслано\n\nbody"
    assert entities is None


def test_forwarded_author_of_falls_back_when_get_entity_raises_value_error():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    author = asyncio.run(
        attribution.forwarded_author_of(
            Client(),
            _fwd_message(from_id=types.PeerUser(user_id=9), from_name="Кто-то"),
            {},
        )
    )
    assert author == attribution.Author(text="Кто-то", lead=FORWARD_LEAD)


def test_forwarded_author_of_uses_message_forward_chat_when_get_entity_refuses():
    """Issue #80 / MIAMIVICE #50: GetChannels refuses a private/left origin, but
    the same GetMessages response already carried the Channel (title visible
    in Telegram's forward chrome). Telethon surfaces it as message.forward
    .get_chat() — use that before falling through to bare «Переслано»."""

    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.ChannelPrivateError(request=None)

    class Forward:
        async def get_chat(self):
            return SimpleNamespace(
                id=1629147115,
                title="Комьюнити Арсена Маркаряна",
                username=None,
                usernames=[],
            )

        async def get_sender(self):
            return None

    msg = _fwd_message(from_id=types.PeerChannel(channel_id=1629147115))
    msg.forward = Forward()
    author = asyncio.run(attribution.forwarded_author_of(Client(), msg, {}))
    assert author == attribution.Author(
        text="Комьюнити Арсена Маркаряна", lead=FORWARD_LEAD
    )
    text, _ = attribution.prefixed("body", None, author)
    assert text.startswith("Переслано от Комьюнити Арсена Маркаряна\n\n")
