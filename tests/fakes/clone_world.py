"""A generated channel + discussion group world for clone reply properties.

One hypothesis strategy, ``worlds()``, draws a small source channel, its
discussion group, and what a clone has copied of both so far. Channel ids and
group ids are drawn from the same small range on purpose, as are the two
destination id spaces, so a reply target taken from the wrong chat lands on an
id that exists there too and only the chat tells them apart.

The world also knows the truth a correct clone must reach: where each reply's
parent lives in the destination (``parent_destination``) and which anchor a
comment's thread hangs off (``thread_destination``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from hypothesis import strategies as st
from telethon.tl import types

from tests.fakes.clone import CloneCommentsClient, anchor, message
from tgcli.clone import quotes, state

SOURCE_CHANNEL = 123
SOURCE_GROUP = 55
DESTINATION_CHANNEL = 999
DESTINATION_GROUP = 888

# Source ids: both chats number their messages from the same range.
SOURCE_IDS = range(1, 13)
# Destination ids: the two ranges overlap but differ, so an id from the wrong
# space is sometimes also a real id in the target chat and sometimes not.
DESTINATION_CHANNEL_IDS = range(1, 13)
DESTINATION_GROUP_IDS = range(5, 17)


@dataclass(frozen=True)
class Reply:
    """A reply header's parent: an id in the channel or in the group."""

    space: str  # "channel" or "group"
    id: int


@dataclass(frozen=True)
class Post:
    id: int
    dest: int | None  # destination channel id once copied
    anchor: int | None  # source group id of Telegram's auto-forward
    anchor_present: bool  # the source anchor can still be read
    anchor_remembered: bool  # an earlier run recorded the anchor pair
    dest_anchor: int | None  # the copy's auto-forward in the destination group
    reply: Reply | None = None


@dataclass(frozen=True)
class Comment:
    id: int
    thread: int  # the post whose discussion it belongs to
    dest: int | None  # destination group id once copied
    reply: Reply


@dataclass(frozen=True)
class World:
    posts: tuple[Post, ...]
    comments: tuple[Comment, ...]
    posts_cursor: int
    posts_exhausted: bool
    # A posts window that runs after a comments window in the same sync finds
    # the source and destination groups already on the shared ResolveContext.
    comments_leg_ran: bool

    def post(self, post_id: int) -> Post | None:
        return next((item for item in self.posts if item.id == post_id), None)

    def comment(self, comment_id: int) -> Comment | None:
        return next((item for item in self.comments if item.id == comment_id), None)

    def thread_destination(self, post: Post) -> tuple[str, int] | None:
        """The destination anchor a comment under ``post`` must hang off, when
        the clone can know it: the post is copied, its copy has an anchor, and
        the source anchor is readable or was recorded by an earlier run."""
        if post.dest is None or post.dest_anchor is None:
            return None
        if not (post.anchor_present or post.anchor_remembered):
            return None
        return ("group", post.dest_anchor)

    def parent_destination(self, reply: Reply, *, leg: str) -> tuple[str, int] | None:
        """Where ``reply``'s parent lives in the destination, or None if lost."""
        if reply.space == "channel":
            post = self.post(reply.id)
            if post is None or post.dest is None:
                return None
            if leg == "posts":
                return ("channel", post.dest)
            # A comment quoting a channel post points at that post's
            # discussion anchor in the destination group.
            if post.dest_anchor is None:
                return None
            return ("group", post.dest_anchor)
        comment = self.comment(reply.id)
        if comment is not None:
            return None if comment.dest is None else ("group", comment.dest)
        post = next((item for item in self.posts if item.anchor == reply.id), None)
        return None if post is None else self.thread_destination(post)

    def destination_ids(self, chat: str) -> set[int]:
        """Every message id that exists in the destination ``chat``."""
        if chat == "channel":
            return {post.dest for post in self.posts if post.dest is not None}
        return {
            post.dest_anchor for post in self.posts if post.dest_anchor is not None
        } | {comment.dest for comment in self.comments if comment.dest is not None}


def _fresh(draw, pool, used: set[int]) -> int:
    """A not-yet-used id from ``pool``; shrinks toward the smallest free id."""
    found = draw(st.sampled_from([item for item in pool if item not in used]))
    used.add(found)
    return found


@st.composite
def worlds(draw) -> World:
    # Each post and comment draws its own fields in one run, so the shrinker
    # can drop a whole record without reshuffling the others.
    channel_used: set[int] = set()
    group_used: set[int] = set()
    channel_dests: set[int] = set()
    group_dests: set[int] = set()

    posts = []
    for _ in range(draw(st.integers(1, 5))):
        post_id = _fresh(draw, SOURCE_IDS, channel_used)
        copied = draw(st.booleans())
        has_anchor = draw(st.booleans())
        posts.append(
            Post(
                id=post_id,
                dest=_fresh(draw, DESTINATION_CHANNEL_IDS, channel_dests)
                if copied
                else None,
                anchor=_fresh(draw, SOURCE_IDS, group_used) if has_anchor else None,
                anchor_present=draw(st.booleans()),
                anchor_remembered=draw(st.booleans()),
                dest_anchor=_fresh(draw, DESTINATION_GROUP_IDS, group_dests)
                if copied and draw(st.booleans())
                else None,
            )
        )
    posts.sort(key=lambda item: item.id)
    threads = [post for post in posts if post.anchor is not None]

    comments: list[Comment] = []
    for _ in range(draw(st.integers(0, 5)) if threads else 0):
        thread = draw(st.sampled_from(threads))
        assert thread.anchor is not None
        comment_id = _fresh(draw, SOURCE_IDS, group_used)
        kind = draw(st.sampled_from(["anchor", "comment", "post"]))
        if kind == "anchor":
            reply = Reply("group", thread.anchor)
        elif kind == "comment":
            # An earlier comment in the same thread, or one since deleted.
            siblings = [item.id for item in comments if item.thread == thread.id]
            deleted = _unused(group_used)
            reply = Reply("group", draw(st.sampled_from([*siblings, deleted])))
        else:
            # A channel post, copied or not, or a channel id nothing carries.
            reply = Reply("channel", draw(st.sampled_from(SOURCE_IDS)))
        comments.append(
            Comment(
                id=comment_id,
                thread=thread.id,
                dest=_fresh(draw, DESTINATION_GROUP_IDS, group_dests)
                if draw(st.booleans())
                else None,
                reply=reply,
            )
        )

    # A post replies to another post, a comment, an anchor, a deleted id in
    # either chat, or nothing.
    parents = [Reply("channel", post.id) for post in posts]
    parents += [Reply("group", item.id) for item in comments]
    parents += [Reply("group", post.anchor) for post in threads if post.anchor]
    parents += [Reply("channel", _unused(channel_used))]
    parents += [Reply("group", _unused(group_used))]
    posts = [
        replace(
            post,
            reply=draw(
                st.none()
                | st.sampled_from(parents).filter(
                    lambda item, own=post.id: item != Reply("channel", own)
                )
            ),
        )
        for post in posts
    ]

    return World(
        posts=tuple(posts),
        comments=tuple(comments),
        posts_cursor=draw(st.sampled_from([0, *SOURCE_IDS])),
        posts_exhausted=draw(st.booleans()),
        comments_leg_ran=draw(st.booleans()),
    )


def _unused(used) -> int:
    """A source id no message carries: a deleted parent."""
    return next(item for item in SOURCE_IDS if item not in used)


class WorldClient(CloneCommentsClient):
    """The comments fake, able to name the destination group as a reply peer."""

    async def get_input_entity(self, ref):
        if ref is self.destination_group:
            return types.InputPeerChannel(channel_id=DESTINATION_GROUP, access_hash=1)
        return await super().get_input_entity(ref)


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


def post_message(post: Post):
    reply_to = (
        None
        if post.reply is None
        else _header(post.reply, sender="posts", thread_anchor=None)
    )
    return message(post.id, chat=SOURCE_CHANNEL, reply_to=reply_to)


def comment_message(world: World, comment: Comment):
    thread = world.post(comment.thread)
    assert thread is not None
    header = _header(comment.reply, sender="discussion", thread_anchor=thread.anchor)
    return message(comment.id, chat=SOURCE_GROUP, reply_to=header)


def build(world: World) -> tuple[state.CloneState, WorldClient]:
    """The clone state and Telegram client the world describes."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=SOURCE_CHANNEL, source_title="Source channel"
    )
    clone_state.destination_peer_id = DESTINATION_CHANNEL
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = SOURCE_GROUP
    clone_state.discussion_destination_peer_id = DESTINATION_GROUP
    clone_state.discussion_linked = True
    for post in world.posts:
        if post.dest is not None:
            clone_state.record_mapping(post.id, post.dest)
        if (
            post.anchor is not None
            and post.anchor_remembered
            and post.dest_anchor is not None
        ):
            clone_state.record_anchor(post.anchor, post.dest_anchor)
    for comment in world.comments:
        if comment.dest is not None:
            clone_state.record_discussion_mapping(comment.id, comment.dest)

    group_history = [
        anchor(
            post.anchor, post.id, source_channel_id=SOURCE_CHANNEL, chat=SOURCE_GROUP
        )
        for post in world.posts
        if post.anchor is not None and post.anchor_present
    ] + [comment_message(world, comment) for comment in world.comments]
    client = WorldClient([post_message(post) for post in world.posts], group_history)
    client.anchor_ids = {
        post.dest: post.dest_anchor
        for post in world.posts
        if post.dest is not None and post.dest_anchor is not None
    }
    return clone_state, client


def resolve_context(client: WorldClient, *, comments_leg_ran: bool):
    """The per-run context ``clone.sync`` builds, as each leg leaves it."""
    ctx = quotes.ResolveContext(
        tg=client, mutate=client, destination=client.destination
    )
    if comments_leg_ran:
        ctx.source_group = client.source_group
        ctx.source_channel_id = SOURCE_CHANNEL
        ctx.destination_group = client.destination_group
    return ctx
