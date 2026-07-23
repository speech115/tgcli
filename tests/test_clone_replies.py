# tests/test_clone_replies.py
"""Direct unit tests for clone reply mapping."""

from types import SimpleNamespace

import pytest

from telethon.tl import types

from tgcli.clone import legs, replies, state
from tgcli.errors import PolicyError


def _clone_state(id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src"
    )
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    return legs.posts(clone_state)


def _msg(reply_to=None):
    return SimpleNamespace(reply_to=reply_to)


SOURCE = SimpleNamespace(id=2)


def test_no_reply_metadata_returns_none():
    assert replies.target([_msg()], _clone_state(), SOURCE) is None


def test_mapped_reply_returns_destination_target():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    target = replies.target([_msg(header)], _clone_state({5: 105}), SOURCE)
    assert target.reply_to_msg_id == 105
    assert target.top_msg_id is None


def test_unmapped_parent_flattens_to_none():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    assert replies.target([_msg(header)], _clone_state(), SOURCE) is None


def test_story_reply_flattens_to_none():
    header = types.MessageReplyStoryHeader(peer=types.PeerUser(user_id=7), story_id=3)
    assert replies.target([_msg(header)], _clone_state(), SOURCE) is None


def test_cross_peer_reply_is_rejected():
    header = types.MessageReplyHeader(
        reply_to_msg_id=5, reply_to_peer_id=types.PeerChannel(channel_id=999)
    )
    with pytest.raises(PolicyError, match="cross-peer"):
        replies.target([_msg(header)], _clone_state(), SOURCE)


def test_forum_topic_reply_shape_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    with pytest.raises(PolicyError, match="reply shape"):
        replies.target([_msg(header)], _clone_state(), SOURCE)


def test_ephemeral_reply_shape_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    object.__setattr__(header, "reply_to_ephemeral", True)
    with pytest.raises(PolicyError, match="reply shape"):
        replies.target([_msg(header)], _clone_state(), SOURCE)


def test_album_reply_after_leading_item_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    with pytest.raises(PolicyError, match="leading item"):
        replies.target([_msg(), _msg(header)], _clone_state(), SOURCE)
