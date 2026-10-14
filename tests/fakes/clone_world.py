"""A generated channel + discussion group world for clone reply properties.

``worlds()`` draws a small source channel, its discussion group, and what an
earlier ``tg clone sync`` already copied of both. ``build()`` turns a world into
saved clone state plus a ``CloneCommentsClient`` that records every send and
the destination ids Telegram gave it, so a test can run the real
``tg clone sync`` and read back where each reply went.

Channel and group ids are both small and sequential, so the two id spaces
overlap on purpose, in the source and in the destination: a reply target taken
from the wrong chat lands on an id that may exist there too, and only the chat
tells them apart.

The world is chronological the way Telegram is: a comment comes after its
thread's anchor, a reply after its parent, and the comments leg never passed
an anchor whose post the posts leg had not reached.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hypothesis import strategies as st
from telethon.tl import functions, types

from tests.fakes.clone import CloneCommentsClient, anchor, message
from tgcli.clone import state

SOURCE_CHANNEL = 123
SOURCE_GROUP = 55
DESTINATION_CHANNEL = 999
DESTINATION_GROUP = 888

SENDS = (
    functions.messages.ForwardMessagesRequest,
    functions.messages.SendMessageRequest,
    functions.messages.SendMediaRequest,
    functions.messages.SendMultiMediaRequest,
)


@dataclass(frozen=True)
class Reply:
    """A reply header's parent: an id in the channel or in the group."""

    space: str  # "channel" or "group"
    id: int


@dataclass(frozen=True)
class Post:
    id: int
    copied_before: bool  # an earlier run copied it (only below the cursor)
    dest_anchor_kept: bool  # that earlier copy still has its group anchor
    anchor: int | None  # source group id of Telegram's auto-forward
    anchor_present: bool  # the source anchor can still be read
    anchor_remembered: bool  # the earlier run recorded the anchor pair
    reply: Reply | None = None
    album: int | None = None  # grouped_id shared with its neighbours


@dataclass(frozen=True)
class Comment:
    id: int
    thread: int  # the post whose discussion it belongs to
    copied_before: bool  # an earlier run copied it (only below the cursor)
    reply: Reply


@dataclass(frozen=True)
class World:
    posts: tuple[Post, ...]
    comments: tuple[Comment, ...]
    deleted_group_ids: tuple[int, ...]
    posts_cursor: int
    discussion_cursor: int
    window: int  # posts per interleaved window (legs.WINDOW)

    def post(self, post_id: int) -> Post | None:
        return next((item for item in self.posts if item.id == post_id), None)

    def comment(self, comment_id: int) -> Comment | None:
        return next((item for item in self.comments if item.id == comment_id), None)

    def anchored(self, group_id: int) -> Post | None:
        return next((item for item in self.posts if item.anchor == group_id), None)


@st.composite
def worlds(draw) -> World:
    post_ids = sorted(
        draw(st.lists(st.integers(1, 8), min_size=1, max_size=5, unique=True))
    )
    posts_cursor = draw(st.sampled_from([0, *post_ids]))
    posts = []
    for post_id in post_ids:
        before = post_id <= posts_cursor
        copied_before = before and draw(st.booleans())
        kept = copied_before and draw(st.booleans())
        posts.append(
            Post(
                id=post_id,
                copied_before=copied_before,
                dest_anchor_kept=kept,
                anchor=None,
                anchor_present=draw(st.booleans()),
                anchor_remembered=kept and draw(st.booleans()),
            )
        )

    # The group, oldest first: anchors, comments under an earlier anchor, and
    # ids whose message is gone.
    comments: list[Comment] = []
    deleted: list[int] = []
    for group_id in range(1, draw(st.integers(0, 8)) + 1):
        unanchored = [item for item in posts if item.anchor is None]
        threads = [item for item in posts if item.anchor is not None]
        kinds = ["deleted"]
        kinds += ["anchor"] if unanchored else []
        kinds += ["comment"] if threads else []
        kind = draw(st.sampled_from(kinds))
        if kind == "deleted":
            deleted.append(group_id)
        elif kind == "anchor":
            post = draw(st.sampled_from(unanchored))
            posts[posts.index(post)] = replace(post, anchor=group_id)
        else:
            thread = draw(st.sampled_from(threads))
            assert thread.anchor is not None
            siblings = [item.id for item in comments if item.thread == thread.id]
            choice = draw(st.sampled_from(["anchor", "comment", "post"]))
            if choice == "anchor":
                reply = Reply("group", thread.anchor)
            elif choice == "comment" and (siblings or deleted):
                # An earlier comment in the same thread, or one since deleted.
                reply = Reply("group", draw(st.sampled_from(siblings + deleted)))
            else:
                # A channel post, copied or not, or a channel id nothing carries.
                reply = Reply("channel", draw(st.integers(1, max(post_ids) + 1)))
            comments.append(Comment(group_id, thread.id, False, reply))

    # The comments leg stops before an anchor whose post the posts leg has not
    # reached, so everything it already passed sits below such an anchor.
    reachable = [0]
    for group_id in sorted(
        [item.anchor for item in posts if item.anchor is not None]
        + [item.id for item in comments]
        + deleted
    ):
        post = next((item for item in posts if item.anchor == group_id), None)
        if post is not None and post.id > posts_cursor:
            break
        reachable.append(group_id)
    discussion_cursor = draw(st.sampled_from(reachable))
    comments = [
        replace(item, copied_before=draw(st.booleans()))
        if item.id <= discussion_cursor
        else item
        for item in comments
    ]

    # Neighbouring posts may form an album, which the clone forwards in one
    # request carrying several ids. Album posts carry no reply here.
    albums: list[int | None] = [None] * len(posts)
    for index in range(1, len(posts)):
        if draw(st.booleans()):
            albums[index] = albums[index - 1] or posts[index - 1].id
            albums[index - 1] = albums[index]

    # A post replies to an earlier post, a comment, an anchor, a deleted id in
    # either chat, or nothing.
    replied = []
    for post, album in zip(posts, albums, strict=True):
        if album is not None:
            replied.append(replace(post, album=album))
            continue
        parents = [Reply("channel", item.id) for item in posts if item.id < post.id]
        parents += [
            Reply("channel", item) for item in range(1, post.id) if item not in post_ids
        ]
        parents += [Reply("group", item.id) for item in comments]
        parents += [
            Reply("group", item.anchor)
            for item in posts
            if item.anchor is not None and item.id < post.id
        ]
        parents += [Reply("group", item) for item in deleted]
        reply = draw(st.none() | st.sampled_from(parents)) if parents else None
        replied.append(replace(post, reply=reply))

    return World(
        posts=tuple(replied),
        comments=tuple(comments),
        deleted_group_ids=tuple(deleted),
        posts_cursor=posts_cursor,
        discussion_cursor=discussion_cursor,
        window=draw(st.sampled_from([1, 2, 50])),
    )


class WorldClient(CloneCommentsClient):
    """The comments fake with Telegram's anchors and a record of every send.

    Telegram auto-forwards each new channel post into the linked group, so a
    post sent here gets its destination anchor at once. ``sent`` maps each
    destination message, as (chat, id), to the request that created it.
    ``copies`` maps each source message, as (chat, id), to the destination id
    Telegram gave its copy: what the fake itself answered, never what clone
    state recorded, plus the earlier run's copies the test seeded.
    """

    def __init__(self, messages, comments=()):
        super().__init__(messages, comments)
        self.sent: dict[tuple[str, int], object] = {}
        self.copies: dict[tuple[str, int], int] = {}

    async def get_input_entity(self, ref):
        if ref is self.destination_group:
            return types.InputPeerChannel(channel_id=DESTINATION_GROUP, access_hash=1)
        return await super().get_input_entity(ref)

    async def __call__(self, request):
        response = await super().__call__(request)
        if isinstance(request, SENDS):
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            chat = "group" if peer is self.destination_group else "channel"
            assigned = {
                update.random_id: update.id
                for update in response.updates
                if isinstance(update, types.UpdateMessageID)
            }
            for source_id, random_id in _sources(request):
                destination_id = assigned[random_id]
                self.copies[(chat, source_id)] = destination_id
                self.sent[(chat, destination_id)] = request
                if chat == "channel":
                    self.group_last_id += 1
                    self.anchor_ids[destination_id] = self.group_last_id
        return response


def _text(source_id: int) -> str:
    """Each source message's own body, so a reuploaded copy names its source."""
    return f"source message #{source_id}"


def _sources(request) -> list[tuple[int, int]]:
    """(source id, random id) for each message a send request carries."""
    if isinstance(request, functions.messages.ForwardMessagesRequest):
        return list(zip(request.id, request.random_id, strict=True))
    if isinstance(request, functions.messages.SendMultiMediaRequest):
        items = [(item.message, item.random_id) for item in request.multi_media]
    else:
        items = [(request.message, request.random_id)]
    found = []
    for body, random_id in items:
        match = re.search(r"source message #(\d+)", body or "")
        assert match is not None, f"a send names no source message: {body!r}"
        found.append((int(match.group(1)), random_id))
    return found


def _header(reply: Reply, *, sender: str, thread_anchor: int | None):
    """The header Telegram puts on a message replying to ``reply``."""
    if sender == "posts":
        peer = None if reply.space == "channel" else types.PeerChannel(SOURCE_GROUP)
        return types.MessageReplyHeader(reply_to_msg_id=reply.id, reply_to_peer_id=peer)
    if reply.space == "group" and reply.id == thread_anchor:
        # A top-level comment replies to the anchor itself and has no top.
        return types.MessageReplyHeader(reply_to_msg_id=reply.id)
    peer = types.PeerChannel(SOURCE_CHANNEL) if reply.space == "channel" else None
    return types.MessageReplyHeader(
        reply_to_msg_id=reply.id, reply_to_peer_id=peer, reply_to_top_id=thread_anchor
    )


def build(world: World) -> tuple[state.CloneState, WorldClient]:
    """Save the clone state the earlier run left, and the client to sync with.

    Earlier copies take destination ids from 1 in source order, in each chat:
    posts in the channel; comments and kept post anchors in the group.
    """
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=SOURCE_CHANNEL, source_title="Source channel"
    )
    clone_state.destination_peer_id = DESTINATION_CHANNEL
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = SOURCE_GROUP
    clone_state.discussion_destination_peer_id = DESTINATION_GROUP
    clone_state.discussion_linked = True
    clone_state.cursor = world.posts_cursor
    clone_state.discussion_cursor = world.discussion_cursor

    channel_last = 0
    group_last = 0
    anchor_ids = {}
    seeded = {}
    for post in world.posts:
        if not post.copied_before:
            continue
        channel_last += 1
        clone_state.record_mapping(post.id, channel_last)
        seeded[("channel", post.id)] = channel_last
        if post.dest_anchor_kept:
            group_last += 1
            anchor_ids[channel_last] = group_last
            if post.anchor is not None and post.anchor_remembered:
                clone_state.record_anchor(post.anchor, group_last)
    for comment in world.comments:
        if comment.copied_before:
            group_last += 1
            clone_state.record_discussion_mapping(comment.id, group_last)
            seeded[("group", comment.id)] = group_last
    state.save(clone_state)

    posts = [
        message(
            post.id,
            chat=SOURCE_CHANNEL,
            message=_text(post.id),
            grouped_id=post.album,
            reply_to=None
            if post.reply is None
            else _header(post.reply, sender="posts", thread_anchor=None),
        )
        for post in world.posts
    ]
    group = [
        anchor(
            post.anchor, post.id, source_channel_id=SOURCE_CHANNEL, chat=SOURCE_GROUP
        )
        for post in world.posts
        if post.anchor is not None and post.anchor_present
    ]
    for comment in world.comments:
        thread = world.post(comment.thread)
        assert thread is not None
        header = _header(
            comment.reply, sender="discussion", thread_anchor=thread.anchor
        )
        group.append(
            message(
                comment.id,
                chat=SOURCE_GROUP,
                message=_text(comment.id),
                reply_to=header,
            )
        )
    client = WorldClient(posts, group)
    client.destination_last_id = max(channel_last, 1)
    client.group_last_id = max(group_last, 1)
    client.anchor_ids = anchor_ids
    client.copies = dict(seeded)
    return clone_state, client
