# tests/test_clone_replies.py
"""Direct unit tests for clone reply classification (ADR-0036)."""

from types import SimpleNamespace

import pytest

from telethon.tl import types

from tgcli.clone import legs, replies, state
from tgcli.errors import PolicyError


def _posts(id_map=None, *, discussion_source_peer_id=None, discussion_id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src"
    )
    clone_state.discussion_source_peer_id = discussion_source_peer_id
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    for source_id, destination_id in (discussion_id_map or {}).items():
        clone_state.record_discussion_mapping(source_id, destination_id)
    return legs.posts(clone_state)


def _discussion(id_map=None, *, discussion_id_map=None, source_peer_id=2):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=source_peer_id, source_title="src"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 4454061248
    clone_state.discussion_destination_peer_id = 99
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    for source_id, destination_id in (discussion_id_map or {}).items():
        clone_state.record_discussion_mapping(source_id, destination_id)
    return legs.discussion(clone_state)


def _msg(reply_to=None):
    return SimpleNamespace(reply_to=reply_to)


SOURCE = SimpleNamespace(id=2)
DISCUSSION_SOURCE = SimpleNamespace(id=4454061248)


def test_no_reply_metadata_returns_none():
    assert replies.target([_msg()], _posts(), SOURCE) is None


def test_mapped_in_leg_reply_classifies_with_parent():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    classified = replies.target([_msg(header)], _posts({5: 105}), SOURCE)
    assert classified.kind == "mapped-in-leg"
    assert classified.parent_id == 5
    assert classified.top_id is None


def test_unmapped_parent_classifies_as_flatten():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    classified = replies.target([_msg(header)], _posts(), SOURCE)
    assert classified.kind == "flatten"
    assert classified.parent_id == 5


def test_story_reply_classifies_as_flatten():
    header = types.MessageReplyStoryHeader(peer=types.PeerUser(user_id=7), story_id=3)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_foreign_peer_reply_classifies_not_raises():
    header = types.MessageReplyHeader(
        reply_to_msg_id=5, reply_to_peer_id=types.PeerChannel(channel_id=999)
    )
    classified = replies.target([_msg(header)], _posts(), SOURCE)
    assert classified.kind == "foreign-peer"
    assert classified.parent_id == 5
    assert isinstance(classified.peer, types.PeerChannel)


def test_forum_topic_on_nonforum_classifies_as_flatten():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_ephemeral_reply_classifies_as_flatten():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    object.__setattr__(header, "reply_to_ephemeral", True)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_reply_from_and_reply_media_do_not_reject_mapped_reply():
    header = types.MessageReplyHeader(
        reply_to_msg_id=5,
        reply_from=types.MessageFwdHeader(date=None),
        reply_media=types.MessageMediaPhoto(),
    )
    classified = replies.target([_msg(header)], _posts({5: 105}), SOURCE)
    assert classified.kind == "mapped-in-leg"


def test_album_reply_after_leading_item_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    with pytest.raises(PolicyError, match="leading item"):
        replies.target([_msg(), _msg(header)], _posts(), SOURCE)


def test_source_2374_foreign_quote_classifies_as_foreign_peer():
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=types.PeerChannel(2275285084),
        reply_from=types.MessageFwdHeader(date=None),
        reply_media=types.MessageMediaPhoto(),
        quote_text="что это де-факто не наставничество…",
        reply_to_top_id=2373,
    )
    classified = replies.target(
        [_msg(header)],
        _discussion(discussion_id_map={2373: 900}),
        DISCUSSION_SOURCE,
    )
    assert classified.kind == "foreign-peer"
    assert classified.parent_id == 1244
    assert classified.quote_text == "что это де-факто не наставничество…"
    assert classified.peer.channel_id == 2275285084


def test_source_2378_channel_quote_classifies_as_mapped_cross_leg():
    header = types.MessageReplyHeader(
        reply_to_msg_id=789,
        reply_to_peer_id=types.PeerChannel(2),
        quote_text="quoted post",
        reply_to_top_id=2377,
    )
    classified = replies.target(
        [_msg(header)],
        _discussion(id_map={789: 773}, discussion_id_map={2377: 901}),
        DISCUSSION_SOURCE,
    )
    assert classified.kind == "mapped-cross-leg"
    assert classified.parent_id == 789
    assert classified.quote_text == "quoted post"
