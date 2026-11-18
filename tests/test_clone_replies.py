# tests/test_clone_replies.py
"""Direct unit tests for clone reply classification."""

from types import SimpleNamespace

import pytest
from telethon.tl import types

from tests.fakes.clone import discussion
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


def _msg(reply_to=None):
    return SimpleNamespace(reply_to=reply_to)


SOURCE = SimpleNamespace(id=2)
DISCUSSION_SOURCE = SimpleNamespace(id=4454061248)


def test_no_reply_metadata_returns_none():
    assert replies.target([_msg()], _posts(), SOURCE) is None


def test_story_reply_classifies_as_flatten():
    header = types.MessageReplyStoryHeader(peer=types.PeerUser(user_id=7), story_id=3)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_forum_topic_on_nonforum_classifies_as_flatten():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_ephemeral_reply_classifies_as_flatten():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    object.__setattr__(header, "reply_to_ephemeral", True)
    assert replies.target([_msg(header)], _posts(), SOURCE).kind == "flatten"


def test_album_reply_after_leading_item_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    with pytest.raises(PolicyError, match="leading item"):
        replies.target([_msg(), _msg(header)], _posts(), SOURCE)


def test_album_with_divergent_foreign_peers_is_rejected():
    first = types.MessageReplyHeader(
        reply_to_msg_id=5, reply_to_peer_id=types.PeerChannel(999)
    )
    second = types.MessageReplyHeader(
        reply_to_msg_id=5, reply_to_peer_id=types.PeerChannel(1000)
    )
    with pytest.raises(PolicyError, match="inconsistent"):
        replies.target([_msg(first), _msg(second)], _posts(), SOURCE)


def test_cross_leg_parent_beyond_posts_cursor_defers():
    """Discussion-leg comment whose channel parent is newer than the posts
    cursor must defer — not flatten — so windowed interleaving cannot plant
    a permanent flat reply."""
    header = types.MessageReplyHeader(
        reply_to_msg_id=789,
        reply_to_peer_id=types.PeerChannel(2),
        quote_text="quoted post",
        reply_to_top_id=2377,
    )
    classified = replies.target(
        [_msg(header)],
        discussion(discussion_id_map={2377: 901}),
        DISCUSSION_SOURCE,
        posts_cursor=100,
    )
    assert classified.kind == "deferred"
    assert classified.parent_id == 789
