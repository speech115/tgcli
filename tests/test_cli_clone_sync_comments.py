"""clone sync: the comments leg, discussion threads and windows."""

import json

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import (
    make_session_fake,
)
from tests.fakes.clone import (
    CloneCommentsClient,
    anchor,
    forwarded_ids,
    group_sends,
    message,
    seed_comments_clone,
)
from tests.fakes.clone_env import config_env as config_env
from tgcli.cli import main
from tgcli.clone import state


def test_sync_anchor_for_skipped_post_does_not_stall_comments(
    config_env, monkeypatch, capsys
):
    """An anchor for an unsupported/skipped source post is skipped and does
    not stall the comments leg forever."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 50)
    dice = message(2, media=type("MessageMediaDice", (), {})())
    seed_comments_clone()
    client = CloneCommentsClient(
        [dice, message(3, message="ok")],
        [
            anchor(10, 2),
            anchor(11, 3),
            message(
                12,
                message="on 3",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=11),
            ),
        ],
    )
    client.anchor_ids = {3: 600}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] >= 1
    done = state.load(state.clone_id(42, 123, "broadcast"))
    assert done is not None
    assert done.cursor >= 3
    assert done.discussion_cursor >= 12
    assert done.discussion_dest_for(12) is not None


def test_sync_keeps_the_quote_of_a_direct_comment(config_env, monkeypatch, capsys):
    """A comment replying straight to the anchor gets a rebuilt reply header;
    its quote must survive the rebuild."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="agreed",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=10,
                    quote=True,
                    quote_text="the claim",
                    quote_offset=4,
                    quote_entities=[types.MessageEntityBold(offset=0, length=3)],
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [send] = group_sends(client)
    assert send.reply_to.reply_to_msg_id == 500
    assert send.reply_to.quote_text == "the claim"
    assert send.reply_to.quote_offset == 4
    assert send.reply_to.quote_entities == [types.MessageEntityBold(offset=0, length=3)]


def test_sync_defers_cross_leg_comment_beyond_posts_cursor(
    config_env, monkeypatch, capsys
):
    """Comment quoting a channel post newer than the posts cursor must leave
    discussion_cursor unmoved past that comment and send nothing; once the
    post is mapped, the next run copies it."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    clone_state = seed_comments_clone()

    class FloodBeforeParent(CloneCommentsClient):
        async def __call__(self, request):
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            if (
                peer is self.destination
                and isinstance(request, functions.messages.ForwardMessagesRequest)
                and list(request.id) == [5]
            ):
                self.requests.append(request)
                raise telethon_errors.FloodWaitError(request=request, capture=61)
            return await super().__call__(request)

    client = FloodBeforeParent(
        [message(2), message(3), message(5, message="parent post")],
        [
            anchor(10, 2),
            message(
                12,
                message="waiting",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=5,
                    reply_to_peer_id=types.PeerChannel(123),
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600, 5: 700}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    capsys.readouterr()
    assert group_sends(client) == []
    mid = state.load(clone_state.clone_id)
    assert mid is not None
    assert mid.discussion_cursor == 10
    assert mid.discussion_dest_for(12) is None
    assert mid.cursor == 3
    assert mid.dest_for(5) is None

    mid.retry_not_before = None
    state.save(mid)

    client2 = CloneCommentsClient(
        [message(5, message="parent post")],
        [
            anchor(10, 2),
            message(
                12,
                message="waiting",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=5,
                    reply_to_peer_id=types.PeerChannel(123),
                ),
            ),
        ],
    )
    client2.destination_last_id = max(mid.max_destination_id() or 0, 1)
    client2.group_last_id = max(mid.max_discussion_destination_id() or 0, 1)
    client2.anchor_ids = {5: 700}
    make_session_fake(monkeypatch, client2)
    assert main(["clone", "sync", "@source", "--json"]) == 0
    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] >= 2
    assert group_sends(client2)
    done = state.load(clone_state.clone_id)
    assert done is not None
    assert done.dest_for(5) is not None
    assert done.discussion_dest_for(12) is not None
    assert done.discussion_cursor >= 12


def test_sync_points_channel_post_at_comment_copied_by_an_earlier_run(
    config_env, monkeypatch, capsys
):
    """On a later run the posts window runs before the comments leg: the
    destination group comes from clone state, so the reply still names the
    group instead of flattening for good or pointing at a channel message
    that merely shares the group comment's id."""
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)

    class GroupInputClient(CloneCommentsClient):
        async def get_input_entity(self, ref):
            if ref is self.destination_group:
                return types.InputPeerChannel(channel_id=888, access_hash=1)
            return await super().get_input_entity(ref)

    client = GroupInputClient(
        [
            message(2),
            message(
                4,
                message="answer",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=12, reply_to_peer_id=types.PeerChannel(55)
                ),
            ),
        ],
        [anchor(10, 2), message(12)],
    )
    client.destination_last_id = 2
    client.group_last_id = 3
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reply_flattened"] == 0
    [post] = [
        item
        for item in client.requests
        if getattr(item, "peer", None) is client.destination
        or getattr(item, "to_peer", None) is client.destination
    ]
    assert isinstance(post, functions.messages.SendMessageRequest)
    assert (
        post.reply_to.to_dict()
        == types.InputReplyToMessage(
            reply_to_msg_id=3,
            reply_to_peer_id=types.InputPeerChannel(channel_id=888, access_hash=1),
        ).to_dict()
    )


def test_sync_limit_spends_phase_one_first(config_env, monkeypatch, capsys):
    """--limit 1 with pending posts and comments: only a post batch is
    copied, more is True, discussion_cursor unchanged.

    Under the interleave the shared budget still ends the run when posts exhaust
    it inside a window (limit < WINDOW); comments are not entered.
    """
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "1", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["remaining"] is True
    sync = result["sync"]
    assert sync["copied"] == 1
    assert sync["more"] is True
    assert sync["discussion_cursor"] == 0
    assert sync["skipped_autoforward"] == 0
    assert group_sends(client) == []
    assert forwarded_ids(client.requests) == [2]
    assert client.iter_messages_calls == [("posts", 0)]
    assert state.load(clone_state.clone_id).discussion_cursor == 0


def test_sync_limit_smaller_than_window_saves_both_cursors(
    config_env, monkeypatch, capsys
):
    """--limit smaller than one window still terminates and saves both
    cursors after a posts+comments window that fits under the budget."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 50)
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            message(
                12,
                message="on 2",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            anchor(13, 3),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "3", "--json"]) == 0
    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 3
    assert sync["more"] is True
    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.cursor == 3
    assert saved.discussion_cursor >= 12
    assert saved.dest_for(2) is not None
    assert saved.discussion_dest_for(12) is not None


def test_sync_warns_when_a_run_leaves_comments_unstarted(
    config_env, monkeypatch, capsys
):
    """Posts copied, comments never reached: the destination group holds only
    Telegram's anchors and reads as a duplicate of the channel. Say so."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "1", "--json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["sync"]["discussion_cursor"] == 0
    assert "warning: clone comments not started" in captured.err


def test_sync_warns_on_resume_while_comments_are_still_unstarted(
    config_env, monkeypatch, capsys
):
    """The misleading state outlives the run that created it: a clone resumed
    after any interruption warns before it does more work, exactly once."""
    clone_state = seed_comments_clone()
    clone_state.record_mapping(2, 2)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2)])
    client.destination_last_id = 2
    client.anchor_ids = {2: 2}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "1", "--json"]) == 0

    assert capsys.readouterr().err.count("warning: clone comments not started") == 1


def test_sync_stays_quiet_once_the_comments_leg_has_progressed(
    config_env, monkeypatch, capsys
):
    """A discussion cursor past zero means the group is no longer a bare
    anchor list — nothing to warn about."""
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    client.destination_last_id = 2
    client.group_last_id = 3
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert "warning: clone comments not started" not in capsys.readouterr().err


def test_sync_blocks_unexpected_discussion_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    client.destination_last_id = 2
    client.group_last_id = 4
    client.group_tail = {4: message(4, message="stray")}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err.splitlines()[-1])["error"]
    assert "discussion destination has unexpected tail" in error["message"]
    assert error["unexpected"] == 1


def test_sync_copies_remaining_posts_alone_after_comments_degrade_mid_run(
    config_env, monkeypatch, capsys
):
    """A group that turns private in window 1 degrades comments.
    The interleave loop used to call the comments leg again in window 2: it
    re-read the group from cursor 0 into a clone marked "unavailable", and
    that state no longer loaded."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 1)
    clone_state = seed_comments_clone()

    class TurnsPrivateOnce(CloneCommentsClient):
        refused = False

        async def iter_messages(
            self, entity, *, min_id=0, reverse=False, wait_time=None
        ):
            if entity is self.source_group and not self.refused:
                self.refused = True
                self.iter_messages_calls.append(("group", min_id))
                raise telethon_errors.ChannelPrivateError(request=None)
            async for item in super().iter_messages(
                entity, min_id=min_id, reverse=reverse
            ):
                yield item

    client = TurnsPrivateOnce(
        [message(2), message(3), message(4)], [anchor(10, 2), message(12)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 3
    assert [call for call in client.iter_messages_calls if call[0] == "group"] == [
        ("group", 0)
    ]
    assert forwarded_ids(client.requests) == [2, 3, 4]
    assert group_sends(client) == []
    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.comments == "unavailable"
    assert [saved.dest_for(post) is not None for post in (2, 3, 4)] == [True] * 3
