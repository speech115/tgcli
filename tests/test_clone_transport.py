"""Unit tests for the pure clone transport decision."""

from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import legs, state, transport


def _clone_state(kind="broadcast", id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src", source_kind=kind
    )
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    return legs.posts(clone_state)


def _source(noforwards=False):
    return SimpleNamespace(id=2, noforwards=noforwards)


def _msg(message_id=10, *, reply_to=None, media=None, noforwards=False, fwd_from=None):
    return SimpleNamespace(
        id=message_id,
        reply_to=reply_to,
        media=media,
        noforwards=noforwards,
        fwd_from=fwd_from,
    )


def test_plain_broadcast_message_is_forwarded():
    plan = transport.decide([_msg()], _clone_state(), _source())
    assert plan.mode == "forwarded"
    assert plan.reply_to is None
    assert plan.reply_flattened is False
    assert plan.needs_author is False


def test_forum_placement_only_reply_is_not_flattened():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    plan = transport.decide(
        [_msg(reply_to=header)], _clone_state(kind="forum"), _source()
    )
    assert plan.mode == "forwarded"
    assert plan.reply_flattened is False
