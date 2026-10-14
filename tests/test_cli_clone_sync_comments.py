"""clone sync: the comments leg, discussion threads and windows."""

import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import (
    TRANSIENT_ERRORS,
    make_session_fake,
    transient_exit_code,
)
from tests.fakes.clone import (
    CloneCommentsClient,
    CloneSyncClient,
    anchor,
    group_sends,
    message,
    seed_clone,
    seed_comments_clone,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli.cli import main
from tgcli.clone import state


def test_sync_copies_posts_before_comments(config_env, monkeypatch, capsys):
    """Phase 1 sends every post before phase 2 sends any comment.

    For a source smaller than one window this remains byte-identical to the
    pre-ADR-0051 sequential ordering.
    """
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            anchor(11, 3),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 3
    sends = [
        item
        for item in client.requests
        if isinstance(
            item,
            (
                functions.messages.ForwardMessagesRequest,
                functions.messages.SendMessageRequest,
            ),
        )
    ]
    peers = [item.to_peer if hasattr(item, "to_peer") else item.peer for item in sends]
    assert peers == [client.destination, client.destination, client.destination_group]


def _send_peers(client):
    return [
        item.to_peer if hasattr(item, "to_peer") else item.peer
        for item in client.requests
        if isinstance(
            item,
            (
                functions.messages.ForwardMessagesRequest,
                functions.messages.SendMessageRequest,
            ),
        )
    ]


def test_sync_interleaves_posts_and_comments_in_windows(
    config_env, monkeypatch, capsys
):
    """More posts than WINDOW with comments on early posts: request order is
    posts×WINDOW, then comments for those posts, then the next posts window
    (ADR-0051)."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3), message(4), message(5)],
        [
            anchor(10, 2),
            message(
                12,
                message="on 2",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            anchor(13, 3),
            message(
                14,
                message="on 3",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=13),
            ),
            anchor(15, 4),
            anchor(16, 5),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600, 4: 700, 5: 800}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 6
    peers = _send_peers(client)
    # Window 1: posts 2,3 → comments 12,14 → window 2: posts 4,5.
    assert peers == [
        client.destination,
        client.destination,
        client.destination_group,
        client.destination_group,
        client.destination,
        client.destination,
    ]


def test_sync_small_source_matches_sequential_request_order(
    config_env, monkeypatch, capsys
):
    """A source smaller than one window keeps today's posts-then-comments
    request sequence."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 50)
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            anchor(11, 3),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    assert _send_peers(client) == [
        client.destination,
        client.destination,
        client.destination_group,
    ]


def test_sync_disabled_comments_does_not_interleave(config_env, monkeypatch, capsys):
    """comments disabled/none: no phase-2 entry and no windowed re-entry."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    for mode in ("disabled", "none"):
        clone_state = seed_clone()
        clone_state.comments = mode
        state.save(clone_state)
        client = CloneSyncClient([message(2), message(3), message(4)])
        make_session_fake(monkeypatch, client)
        assert main(["clone", "sync", "@source", "--json"]) == 0
        capsys.readouterr()
        assert client.iter_messages_calls == [(0, True)]
        assert all(
            (getattr(item, "to_peer", None) or getattr(item, "peer", None))
            is client.destination
            for item in client.requests
        )


def test_sync_flood_mid_window_keeps_prior_window_comments(
    config_env, monkeypatch, capsys
):
    """FloodWait on the first send of window 2 leaves window 1's posts and
    their comments durably mapped, both cursors resumable.

    The capture is past `SHORT_WAIT` on purpose: this pins the exit-5 path, not
    ADR-0052's foreground retry, and a shorter one would make the suite sleep.
    """
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    clone_state = seed_comments_clone()

    class FloodWindowClient(CloneCommentsClient):
        async def __call__(self, request):
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            if (
                peer is self.destination
                and isinstance(request, functions.messages.ForwardMessagesRequest)
                and list(request.id) == [4]
            ):
                self.requests.append(request)
                raise telethon_errors.FloodWaitError(request=request, capture=61)
            return await super().__call__(request)

    client = FloodWindowClient(
        [message(2), message(3), message(4)],
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
    client.anchor_ids = {2: 500, 3: 600, 4: 700}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    capsys.readouterr()
    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.dest_for(2) is not None
    assert saved.dest_for(3) is not None
    assert saved.dest_for(4) is None
    assert saved.discussion_dest_for(12) is not None
    assert saved.cursor == 3
    assert saved.discussion_cursor >= 12


def test_sync_post_reply_after_a_comments_window_keeps_its_channel_target(
    config_env, monkeypatch, capsys
):
    """A post in a later window replying to an earlier post points at that
    post's destination copy. The group message sharing the parent's numeric id
    anchors another post; the posts leg must not read it as a thread root."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()
    client = CloneCommentsClient(
        [
            message(2),
            message(3),
            message(4, reply_to=types.MessageReplyHeader(reply_to_msg_id=2)),
        ],
        [anchor(1, 2), anchor(2, 3), anchor(3, 4)],
    )
    client.anchor_ids = {2: 500, 3: 600, 4: 700}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()
    [reply] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
        and item.peer is client.destination
    ]
    assert reply.reply_to.reply_to_msg_id == 2


def test_sync_comments_phase_stops_at_anchor_beyond_posts_cursor(
    config_env, monkeypatch, capsys
):
    """Phase-2 scan stops at the first anchor whose source post id is newer
    than the posts cursor and does not read past it (ADR-0051 bound)."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()

    class TrackingCommentsClient(CloneCommentsClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.scans: list[list[int]] = []

        async def iter_messages(self, entity, *, min_id=0, reverse=False):
            if entity is not self.source_group:
                async for item in super().iter_messages(
                    entity, min_id=min_id, reverse=reverse
                ):
                    yield item
                return
            self.iter_messages_calls.append(("group", min_id, reverse))
            scan: list[int] = []
            self.scans.append(scan)
            for item in sorted(self.comments, key=lambda value: value.id):
                if item.id > min_id:
                    scan.append(item.id)
                    yield item

    client = TrackingCommentsClient(
        [message(2), message(3), message(4)],
        [
            anchor(10, 2),
            anchor(11, 3),
            anchor(20, 4),
            message(30, message="late"),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600, 4: 700}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()
    assert client.scans, "comments phase must enter at least once"
    first = client.scans[0]
    assert 20 in first
    assert 30 not in first


def test_sync_late_comment_on_older_post_picked_up_in_later_window(
    config_env, monkeypatch, capsys
):
    """A comment written later against an older post (its group id lies beyond
    the bound) is copied in a later window, not dropped."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3), message(4)],
        [
            anchor(10, 2),
            anchor(11, 3),
            anchor(20, 4),
            message(
                30,
                message="late on 2",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600, 4: 700}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["remaining"] is False
    sync = result["sync"]
    assert sync["copied"] == 4  # 3 posts + 1 late comment
    assert group_sends(client)
    done = state.load(state.clone_id(42, 123, "broadcast"))
    assert done is not None
    assert done.discussion_dest_for(30) is not None
    assert done.discussion_cursor >= 30
    assert done.cursor >= 4


def test_sync_anchor_for_skipped_post_does_not_stall_comments(
    config_env, monkeypatch, capsys
):
    """An anchor for an unsupported/skipped source post is skipped and does
    not stall the comments leg forever."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 50)
    # Post 2 is a dice (unsupported); cursor still advances past it.
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


def test_sync_snapshots_discussion_roster_when_comments_enabled(
    config_env, monkeypatch, capsys
):
    """The source channel refuses its roster (not admin) but its readable
    discussion group is snapshotted to the participant sidecar."""
    from tgcli.clone import roster

    clone_state = seed_comments_clone()
    client = CloneCommentsClient([message(2)], [anchor(10, 2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    participants = json.loads(capsys.readouterr().out)["sync"]["participants"]
    assert participants["source"]["status"] == "unavailable"
    assert participants["discussion"]["status"] == "collected"
    assert participants["discussion"]["count"] == 1
    lines = [
        json.loads(line)
        for line in roster.path_for(clone_state.clone_id).read_text().splitlines()
    ]
    assert lines == [
        {
            "peer": "discussion",
            "id": 701,
            "username": "member",
            "first_name": "Group",
            "last_name": None,
            "phone": None,
            "is_bot": False,
        }
    ]


def test_sync_disabled_comments_skips_comment_phase_and_discussion_roster(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.comments = "disabled"
    state.save(clone_state)

    class TrackingClient(CloneSyncClient):
        def __init__(self):
            super().__init__([message(2)])
            self.iter_participants_calls = []

        async def iter_participants(self, entity, limit=None):
            self.iter_participants_calls.append(entity)
            if False:
                yield None

    client = TrackingClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["discussion_cursor"] == 0
    assert result["sync"]["participants"]["discussion"] == {
        "peer_id": None,
        "status": "none",
        "count": 0,
        "reason": None,
    }
    assert client.iter_participants_calls == [client.source]
    # posts-only: never walks a discussion entity
    assert client.iter_messages_calls == [(0, True)]
    assert state.load(clone_state.clone_id).discussion_id_map == {}


def test_sync_skips_source_autoforwards(config_env, monkeypatch, capsys):
    """Anchors in the source discussion group are not copied;
    sync["skipped_autoforward"] counts them."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)], [anchor(10, 2), anchor(11, 3)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["skipped_autoforward"] == 2
    assert sync["copied"] == 2
    assert sync["discussion_cursor"] == 11
    assert group_sends(client) == []
    assert state.load(clone_state.clone_id).discussion_id_map == {}


def test_sync_refuses_to_post_before_the_discussion_group_is_linked(
    config_env, monkeypatch, capsys
):
    """Init created the channel and enabled comments, then died before linking
    the group (a FLOOD_WAIT on the second peer does exactly this, live-proven).
    Posting now would burn every anchor: Telegram only creates them at send
    time with the link already in place, and they cannot be backfilled. Refuse
    instead, and send the user back to init."""
    seed_comments_clone(discussion_destination_peer_id=None, discussion_linked=False)
    client = CloneCommentsClient([message(2), message(3)], [])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert "not linked" in error["message"]
    assert client.requests == []


def test_sync_skips_album_autoforward_anchors(config_env, monkeypatch, capsys):
    """Telegram auto-forwards an album post as an album, so its anchor arrives
    as a multi-message batch. It is still an anchor and must not be copied."""
    clone_state = seed_comments_clone()
    album = [
        message(
            2,
            grouped_id=7,
            media=types.MessageMediaPhoto(
                photo=types.Photo(
                    id=1,
                    access_hash=1,
                    file_reference=b"r",
                    date=None,
                    sizes=[],
                    dc_id=2,
                )
            ),
        ),
        message(
            3,
            grouped_id=7,
            media=types.MessageMediaPhoto(
                photo=types.Photo(
                    id=2,
                    access_hash=2,
                    file_reference=b"r",
                    date=None,
                    sizes=[],
                    dc_id=2,
                )
            ),
        ),
    ]
    anchors = [anchor(10, 2), anchor(11, 3)]
    for item in anchors:
        item.grouped_id = 7
    client = CloneCommentsClient(album, anchors)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["skipped_autoforward"] == 2
    assert sync["discussion_cursor"] == 11
    assert group_sends(client) == []
    assert state.load(clone_state.clone_id).discussion_id_map == {}


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


def test_sync_attaches_a_comment_to_its_post_thread(config_env, monkeypatch, capsys):
    """Comment replying to the source anchor is sent into the destination
    group with reply_to.reply_to_msg_id == the destination anchor id from
    getDiscussionMessage(dest_channel, dest_post_id)."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                from_id=types.PeerUser(77),
                sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex", username="alex")
        return await CloneCommentsClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [lookup] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.GetDiscussionMessageRequest)
    ]
    assert lookup.peer is client.destination and lookup.msg_id == 2
    [send] = group_sends(client)
    assert isinstance(send, functions.messages.SendMessageRequest)
    assert send.message == "Alex (@alex): \n\nnice"
    assert send.reply_to.reply_to_msg_id == 500
    assert send.reply_to.top_msg_id is None
    assert sync["reply_flattened"] == 0
    assert state.load(clone_state.clone_id).discussion_dest_for(12) == 501


def test_sync_remembers_thread_anchors_across_runs(config_env, monkeypatch, capsys):
    """A later run attaches a comment to a thread an earlier run resolved
    without re-reading the source anchor or asking for the destination one."""
    seed_comments_clone()
    thread = [
        anchor(10, 2),
        message(
            12, message="first", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
        ),
    ]
    first = CloneCommentsClient([message(2)], thread)
    first.anchor_ids = {2: 500}
    first.group_last_id = 500
    make_session_fake(monkeypatch, first)
    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    class CountingClient(CloneCommentsClient):
        by_id = 0

        async def get_messages(self, entity, limit=None, ids=None):
            if ids is not None:
                self.by_id += 1
            return await super().get_messages(entity, limit=limit, ids=ids)

    later = message(
        13, message="second", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
    )
    second = CountingClient([message(2)], [*thread, later])
    second.anchor_ids = {2: 500}
    second.destination_last_id = first.destination_last_id
    second.group_last_id = first.group_last_id
    make_session_fake(monkeypatch, second)
    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    [send] = group_sends(second)
    assert send.reply_to.reply_to_msg_id == 500
    assert second.by_id == 0
    assert not any(
        isinstance(item, functions.messages.GetDiscussionMessageRequest)
        for item in second.requests
    )


def test_sync_maps_comment_on_comment_replies(config_env, monkeypatch, capsys):
    """Nested comment: reply_to_msg_id maps through discussion_id_map,
    top_msg_id through the anchor path."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="root",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            message(
                13,
                message="nested",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=12, reply_to_top_id=10
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    root, nested = group_sends(client)
    assert root.reply_to.reply_to_msg_id == 500
    assert nested.reply_to.reply_to_msg_id == 501
    assert nested.reply_to.top_msg_id == 500


def test_sync_flattens_comments_with_unmapped_anchors(config_env, monkeypatch, capsys):
    """Anchor that maps to no destination post: message is still copied,
    sync["reply_flattened"] == 1."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 99),
            message(
                12,
                message="orphan",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["reply_flattened"] == 1
    assert sync["copied"] == 2
    [send] = group_sends(client)
    assert send.reply_to is None
    assert state.load(clone_state.clone_id).discussion_dest_for(12) is not None


def test_sync_keeps_a_comment_with_a_lost_parent_in_its_post_thread(
    config_env, monkeypatch, capsys
):
    """A comment whose parent comment never reached the clone (deleted, or
    skipped as unsupported) still belongs to a known post thread: it lands
    under the destination anchor, never as loose group chatter, and the lost
    parent is still counted in reply_flattened."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                13,
                message="answer to a deleted comment",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=11, reply_to_top_id=10
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["reply_flattened"] == 1
    [send] = group_sends(client)
    assert isinstance(send, functions.messages.SendMessageRequest)
    assert send.reply_to == types.InputReplyToMessage(reply_to_msg_id=500)


def test_sync_defers_cross_leg_comment_beyond_posts_cursor(
    config_env, monkeypatch, capsys
):
    """Comment quoting a channel post newer than the posts cursor must leave
    discussion_cursor unmoved past that comment and send nothing; once the
    post is mapped, the next run copies it (ADR-0051 defer)."""
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

    # Window 1 copies posts 2,3 then comments hit a reply to post 5 → defer.
    # FloodWait on post 5 freezes the mid-defer state for assertions.
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
    assert mid.discussion_cursor == 10  # advanced past anchor, not past comment
    assert mid.discussion_dest_for(12) is None
    assert mid.cursor == 3
    assert mid.dest_for(5) is None

    # Clear the per-clone cooldown so the resume run can proceed. The
    # governor's ledger cooldowns expire on their own; no sidecar to delete.
    mid.retry_not_before = None
    state.save(mid)

    # Resume: parent post maps, then comments leg copies the deferred reply.
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
    # Restore mapped posts 2,3 into the fresh client's view via state only.
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


def test_sync_points_channel_post_at_copied_group_comment(
    config_env, monkeypatch, capsys
):
    """A channel post replying to a comment already copied into the destination
    group sends the reply into that group, never at the channel's own message
    with the group's id."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()

    class GroupInputClient(CloneCommentsClient):
        async def get_input_entity(self, ref):
            if ref is self.destination_group:
                return types.InputPeerChannel(channel_id=888, access_hash=1)
            return await super().get_input_entity(ref)

    # Window 1 copies posts 2,3 and comment 12 (→ 501); window 2 copies post 4.
    client = GroupInputClient(
        [
            message(2),
            message(3),
            message(
                4,
                message="answer",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=12, reply_to_peer_id=types.PeerChannel(55)
                ),
            ),
        ],
        [
            anchor(10, 2),
            message(
                12,
                message="question",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 4
    assert sync["reply_flattened"] == 0
    [post] = [
        item
        for item in client.requests
        if getattr(item, "peer", None) is client.destination
        and getattr(item, "reply_to", None) is not None
    ]
    assert (
        post.reply_to.to_dict()
        == types.InputReplyToMessage(
            reply_to_msg_id=501,
            reply_to_peer_id=types.InputPeerChannel(channel_id=888, access_hash=1),
        ).to_dict()
    )


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


def test_sync_copies_off_thread_group_messages(config_env, monkeypatch, capsys):
    """A plain group message with no reply header clones into the destination
    group through the megagroup transport rules, keeping its author header."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [message(12, message="hello", from_id=types.PeerUser(77), sender_id=77)],
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [send] = group_sends(client)
    assert isinstance(send, functions.messages.ForwardMessagesRequest)
    assert send.from_peer is client.source_group
    assert send.id == [12]
    assert send.drop_author is False


def test_sync_advances_the_discussion_cursor_per_batch(config_env, monkeypatch, capsys):
    """discussion_cursor is saved after each confirmed batch and a rerun
    copies nothing."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12, message="one", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
            ),
            message(
                13, message="two", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)["sync"]
    assert first["discussion_cursor"] == 13
    saved = state.load(clone_state.clone_id)
    assert saved.discussion_cursor == 13
    assert [saved.discussion_dest_for(12), saved.discussion_dest_for(13)] == [501, 502]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)["sync"]
    assert rerun["copied"] == 0
    assert rerun["skipped_autoforward"] == 0
    assert len(group_sends(client)) == 2
    assert client.iter_messages_calls == [
        (0, True),
        ("group", 0, True),
        (2, True),
        ("group", 13, True),
    ]


def test_sync_limit_spends_phase_one_first(config_env, monkeypatch, capsys):
    """--limit 1 with pending posts and comments: only a post batch is
    copied, more is True, discussion_cursor unchanged.

    Under ADR-0051 the shared budget still ends the run when posts exhaust
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
    assert client.iter_messages_calls == [(0, True)]
    assert state.load(clone_state.clone_id).discussion_cursor == 0


def test_sync_limit_spans_both_legs_and_may_return_comments(
    config_env, monkeypatch, capsys
):
    """--limit N counts batches across both legs; with WINDOW < limit a run
    may return comments where pre-ADR-0051 returned only posts."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 2)
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3), message(4), message(5), message(6)],
        [
            anchor(10, 2),
            message(
                12,
                message="on 2",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            anchor(13, 3),
            anchor(14, 4),
            anchor(15, 5),
            anchor(16, 6),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600, 4: 700, 5: 800, 6: 900}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "3", "--json"]) == 0
    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 3
    assert sync["more"] is True
    # 2 posts + 1 comment (shared budget), not 3 posts alone.
    peers = _send_peers(client)
    assert peers == [
        client.destination,
        client.destination,
        client.destination_group,
    ]
    assert sync["discussion_cursor"] >= 12


def test_sync_limit_smaller_than_window_saves_both_cursors(
    config_env, monkeypatch, capsys
):
    """--limit smaller than one window still terminates and saves both
    cursors after a posts+comments window that fits under the budget."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 50)
    clone_state = seed_comments_clone()
    # Two posts only — under WINDOW — so comments run with remaining budget.
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
    assert sync["copied"] == 3  # 2 posts + 1 comment
    # Anchor 13 remains past the budget → more; both cursors still durable.
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


def test_sync_tolerates_destination_autoforwards_in_the_tail(
    config_env, monkeypatch, capsys
):
    """Telegram's anchors in the destination group are not 'unexpected
    tail messages'."""
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    client.destination_last_id = 2
    client.group_last_id = 5
    client.group_tail = {
        4: anchor(4, 2, source_channel_id=999),
        5: anchor(5, 2, source_channel_id=999),
    }
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 0
    assert group_sends(client) == []


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

    # CONTRACT §2: the error is the final single-line JSON object on a stderr
    # that also carries progress lines — not the whole stream.
    error = json.loads(capsys.readouterr().err.splitlines()[-1])["error"]
    assert "discussion destination has unexpected tail" in error["message"]
    assert error["unexpected"] == 1


def test_sync_skips_phase_two_when_comments_are_unavailable(
    config_env, monkeypatch, capsys
):
    """comments == "unavailable": posts copy, no discussion requests,
    skipped_autoforward == 0."""
    seed_comments_clone(
        comments="unavailable",
        discussion_destination_peer_id=None,
        discussion_linked=False,
    )
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_autoforward"] == 0
    assert sync["discussion_cursor"] == 0
    assert group_sends(client) == []
    assert client.iter_messages_calls == [(0, True)]


def test_sync_marks_comments_unavailable_when_source_group_turns_private(
    config_env, monkeypatch, capsys
):
    """Enabled comments whose source group later refuses access must exit 0
    with posts copied and the ADR-0023 marker persisted — not a traceback
    through cli.py's unrecognized-exception path."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])

    async def get_entity(ref):
        if isinstance(ref, types.PeerChannel) and ref.channel_id == 55:
            raise telethon_errors.ChannelPrivateError(request=None)
        return await CloneCommentsClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_autoforward"] == 0
    assert sync["discussion_cursor"] == 0
    assert group_sends(client) == []
    assert ("group", 0, True) not in client.iter_messages_calls
    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.comments == "unavailable"


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_sync_keeps_comments_enabled_after_a_transient_group_read_failure(
    config_env, monkeypatch, capsys, make_error
):
    """One Telegram 500 on the discussion group is not a refusal. It used to
    reach the degrade guard as a ValueError, switch comments off for good,
    and drop the comment progress; now the run stops and the next one retries."""
    clone_state = seed_comments_clone()
    error = make_error()

    class FailingGroupClient(CloneCommentsClient):
        async def iter_messages(self, entity, *, min_id=0, reverse=False):
            if entity is self.source_group:
                raise error
            async for item in super().iter_messages(
                entity, min_id=min_id, reverse=reverse
            ):
                yield item

    client = FailingGroupClient([message(2)], [anchor(10, 2), message(12)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == transient_exit_code(error)

    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.comments == "enabled"
    assert saved.dest_for(2) is not None


def test_sync_copies_remaining_posts_alone_after_comments_degrade_mid_run(
    config_env, monkeypatch, capsys
):
    """A group that turns private in window 1 degrades comments (ADR-0061).
    The interleave loop used to call the comments leg again in window 2: it
    re-read the group from cursor 0 into a clone marked "unavailable", and
    that state no longer loaded."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 1)
    clone_state = seed_comments_clone()

    class TurnsPrivateOnce(CloneCommentsClient):
        refused = False

        async def iter_messages(self, entity, *, min_id=0, reverse=False):
            if entity is self.source_group and not self.refused:
                self.refused = True
                self.iter_messages_calls.append(("group", min_id, reverse))
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
        ("group", 0, True)
    ]
    assert group_sends(client) == []
    saved = state.load(clone_state.clone_id)
    assert saved is not None
    assert saved.comments == "unavailable"
    assert [saved.dest_for(post) is not None for post in (2, 3, 4)] == [True] * 3
