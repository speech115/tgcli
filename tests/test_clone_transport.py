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


def test_protected_source_forces_reupload():
    plan = transport.decide([_msg()], _clone_state(), _source(noforwards=True))
    assert plan.mode == "reuploaded"


def test_protected_message_forces_reupload():
    plan = transport.decide([_msg(noforwards=True)], _clone_state(), _source())
    assert plan.mode == "reuploaded"


def test_mapped_reply_forces_reupload_with_target():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    plan = transport.decide(
        [_msg(reply_to=header)], _clone_state(id_map={5: 105}), _source()
    )
    assert plan.mode == "reuploaded"
    assert isinstance(plan.reply_to, types.InputReplyToMessage)
    assert plan.reply_to.reply_to_msg_id == 105
    assert plan.reply_flattened is False


def test_unmapped_reply_is_flattened_and_forwarded():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    plan = transport.decide([_msg(reply_to=header)], _clone_state(), _source())
    assert plan.mode == "forwarded"
    assert plan.reply_to is None
    assert plan.reply_flattened is True


def test_single_poll_becomes_snapshot():
    media = types.MessageMediaPoll(poll=None, results=None)
    plan = transport.decide([_msg(media=media)], _clone_state(), _source())
    assert plan.mode == "snapshots"


def test_album_never_snapshots():
    media = types.MessageMediaPoll(poll=None, results=None)
    plan = transport.decide(
        [_msg(1, media=media), _msg(2, media=media)], _clone_state(), _source()
    )
    assert plan.mode == "forwarded"


def test_megagroup_needs_author_when_not_forwarded():
    plan = transport.decide(
        [_msg()], _clone_state(kind="megagroup"), _source(noforwards=True)
    )
    assert plan.needs_author is True


def test_megagroup_forward_needs_no_author():
    plan = transport.decide([_msg()], _clone_state(kind="megagroup"), _source())
    assert plan.needs_author is False


def test_forum_placement_only_reply_is_not_flattened():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    plan = transport.decide(
        [_msg(reply_to=header)], _clone_state(kind="forum"), _source()
    )
    assert plan.mode == "forwarded"
    assert plan.reply_flattened is False


def test_broadcast_reupload_of_forward_needs_author():
    """ADR-0050: a reposted broadcast post on the reupload path needs the prefix."""
    fwd = types.MessageFwdHeader(date=None, from_id=types.PeerUser(user_id=7))
    plan = transport.decide(
        [_msg(fwd_from=fwd)], _clone_state(), _source(noforwards=True)
    )
    assert plan.mode == "reuploaded"
    assert plan.needs_author is True


def test_broadcast_reupload_without_forward_needs_no_author():
    plan = transport.decide([_msg()], _clone_state(), _source(noforwards=True))
    assert plan.mode == "reuploaded"
    assert plan.needs_author is False


def test_broadcast_forward_path_needs_no_author_even_with_fwd_from():
    """Native forward keeps Telegram's header — no double attribution."""
    fwd = types.MessageFwdHeader(date=None, from_name="Someone")
    plan = transport.decide([_msg(fwd_from=fwd)], _clone_state(), _source())
    assert plan.mode == "forwarded"
    assert plan.needs_author is False


def test_broadcast_snapshot_of_forward_needs_author():
    fwd = types.MessageFwdHeader(date=None, from_id=types.PeerUser(user_id=7))
    media = types.MessageMediaPoll(poll=None, results=None)
    plan = transport.decide(
        [_msg(media=media, fwd_from=fwd)], _clone_state(), _source(noforwards=True)
    )
    assert plan.mode == "snapshots"
    assert plan.needs_author is True


def test_broadcast_album_forward_uses_leading_message_fwd_from():
    """Leading message's fwd_from governs the batch (author only on index 0)."""
    fwd = types.MessageFwdHeader(date=None, from_name="Someone")
    plan = transport.decide(
        [_msg(1, fwd_from=fwd), _msg(2, fwd_from=fwd)],
        _clone_state(),
        _source(noforwards=True),
    )
    assert plan.mode == "reuploaded"
    assert plan.needs_author is True


def test_broadcast_album_without_leading_fwd_from_needs_no_author():
    plan = transport.decide(
        [_msg(1), _msg(2, fwd_from=types.MessageFwdHeader(date=None, from_name="X"))],
        _clone_state(),
        _source(noforwards=True),
    )
    assert plan.mode == "reuploaded"
    assert plan.needs_author is False
