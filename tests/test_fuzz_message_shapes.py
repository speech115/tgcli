"""Broken Telegram responses must still serialize as one JSON document.

Telegram sends well-typed TL objects, but their *values* can disagree with
each other: an entity whose UTF-16 offset points past the text or into the
middle of an emoji, a reply header naming a peer the chat is not, a document
with a negative size. `message_to_dict` turns every message `tg read`,
`tg search`, `tg message` and `tg changes` print, so one bad message must not
take the page down with it.

Real `telethon.tl.types` objects only (CONTRACT §5): a SimpleNamespace would
let the serializer read fields Telegram never omits.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from hypothesis import given, settings, strategies as st
from telethon.helpers import add_surrogate
from telethon.tl import types

from tgcli.messages import message_rows as to_rows, message_to_dict

DETERMINISTIC = settings(max_examples=300, derandomize=True, deadline=None)
CHAT = types.PeerChannel(channel_id=1234)

TEXT = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)),
    max_size=40,
)
DATES = st.none() | st.datetimes(
    min_value=datetime(1970, 1, 2),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)
IDS = st.integers(min_value=-(2**63), max_value=2**63 - 1)
SMALL = st.integers(min_value=-8, max_value=80)

PEERS = st.none() | st.one_of(
    st.builds(types.PeerUser, user_id=st.integers(1, 2**40)),
    st.builds(types.PeerChat, chat_id=st.integers(1, 2**40)),
    st.builds(types.PeerChannel, channel_id=st.integers(1, 2**40)),
)


def entities(text: str):
    """Entities whose offsets and lengths need not fit the text at all."""
    span = len(add_surrogate(text)) + 6
    offset = st.integers(min_value=-4, max_value=span)
    length = st.integers(min_value=-2, max_value=span)
    one = st.one_of(
        st.builds(types.MessageEntityBold, offset=offset, length=length),
        st.builds(
            types.MessageEntityCustomEmoji,
            offset=offset,
            length=length,
            document_id=IDS,
        ),
        st.builds(types.MessageEntityTextUrl, offset=offset, length=length, url=TEXT),
    )
    return st.lists(one, max_size=6) | st.none()


ATTRIBUTES = st.lists(
    st.one_of(
        st.builds(types.DocumentAttributeFilename, file_name=TEXT),
        st.builds(
            types.DocumentAttributeAudio,
            duration=SMALL,
            voice=st.booleans(),
            title=st.none() | TEXT,
        ),
        st.builds(
            types.DocumentAttributeVideo,
            duration=st.floats(allow_nan=True, allow_infinity=True),
            w=SMALL,
            h=SMALL,
            round_message=st.booleans(),
        ),
    ),
    max_size=3,
)

MEDIA = st.one_of(
    st.none(),
    st.just(types.MessageMediaUnsupported()),
    st.just(types.MessageMediaEmpty()),
    st.builds(types.MessageMediaPhoto, photo=st.just(types.PhotoEmpty(id=1))),
    st.builds(
        types.MessageMediaDocument,
        document=st.builds(
            types.Document,
            id=IDS,
            access_hash=IDS,
            file_reference=st.binary(max_size=4),
            date=DATES,
            mime_type=TEXT,
            size=IDS,
            dc_id=SMALL,
            attributes=ATTRIBUTES,
        ),
    ),
    st.builds(types.MessageMediaDocument, document=st.just(types.DocumentEmpty(id=1))),
)

REACTION = st.one_of(
    st.just(types.ReactionEmpty()),
    st.just(types.ReactionPaid()),
    st.builds(types.ReactionEmoji, emoticon=TEXT),
    st.builds(types.ReactionCustomEmoji, document_id=IDS),
)
REACTIONS = st.none() | st.builds(
    types.MessageReactions,
    results=st.lists(
        st.builds(types.ReactionCount, reaction=REACTION, count=SMALL), max_size=4
    ),
)

REPLY = st.none() | st.builds(
    types.MessageReplyHeader,
    reply_to_msg_id=st.none() | SMALL,
    reply_to_peer_id=PEERS,
    reply_to_top_id=st.none() | SMALL,
    forum_topic=st.booleans(),
    quote_text=st.none() | TEXT,
)

FORWARD = st.none() | st.builds(
    types.MessageFwdHeader,
    date=DATES.filter(lambda value: value is not None),
    from_id=PEERS,
    from_name=st.none() | TEXT,
)


@st.composite
def messages(draw):
    text = draw(TEXT)
    return types.Message(
        id=draw(SMALL),
        peer_id=CHAT,
        date=draw(DATES),
        message=text,
        entities=draw(entities(text)),
        media=draw(MEDIA),
        reply_to=draw(REPLY),
        fwd_from=draw(FORWARD),
        reactions=draw(REACTIONS),
        edit_date=draw(DATES),
        grouped_id=draw(st.none() | IDS),
        out=draw(st.booleans()),
        media_unread=draw(st.booleans()),
    )


CHATS = st.one_of(
    st.none(),
    st.builds(
        types.Channel,
        id=st.integers(1, 2**40),
        title=TEXT,
        photo=st.just(types.ChatPhotoEmpty()),
        date=DATES,
        username=st.none() | st.from_regex(r"[a-z][a-z0-9_]{4,10}", fullmatch=True),
        broadcast=st.booleans(),
        megagroup=st.booleans(),
    ),
)


@DETERMINISTIC
@given(messages(), CHATS)
def test_any_message_serializes_to_utf8_json(message, chat):
    row = message_to_dict(message, chat)

    # What `emit_json` does, then what the stdout encoder does with it.
    encoded = json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")
    assert json.loads(encoded)["id"] == message.id
    assert isinstance(row["text"], str)
    assert all(isinstance(item["emoji"], str) for item in row["custom_emoji"])


@DETERMINISTIC
@given(messages())
def test_plain_rows_survive_any_message(message):
    page = {"messages": [message_to_dict(message, None)]}
    (row,) = to_rows(page)
    assert all(cell is None or isinstance(cell, int | str) for cell in row)
    "\t".join("" if cell is None else str(cell) for cell in row).encode("utf-8")


def test_a_custom_emoji_entity_that_splits_a_surrogate_pair_does_not_crash():
    """Offset 2 / length 1 is the second half of the UTF-16 pair of 😀."""
    message = types.Message(
        id=1,
        peer_id=CHAT,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        message="a😀b",
        entities=[types.MessageEntityCustomEmoji(offset=2, length=1, document_id=9)],
    )

    row = message_to_dict(message)

    (item,) = row["custom_emoji"]
    assert item["id"] == "9"
    json.dumps(row, ensure_ascii=False).encode("utf-8")


def test_a_whole_custom_emoji_keeps_its_glyph():
    message = types.Message(
        id=1,
        peer_id=CHAT,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        message="a😀b",
        entities=[types.MessageEntityCustomEmoji(offset=1, length=2, document_id=9)],
    )

    assert message_to_dict(message)["custom_emoji"][0]["emoji"] == "😀"


@DETERMINISTIC
@given(TEXT.flatmap(lambda text: st.tuples(st.just(text), entities(text))))
def test_draft_custom_emoji_survive_any_entities(case):
    from tgcli.messages import custom_emoji as draft_custom_emoji

    text, found = case
    rows = draft_custom_emoji(text, found)
    json.dumps(rows, ensure_ascii=False).encode("utf-8")
