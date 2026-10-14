"""Property: every clone reply lands where its parent lives (tgcli-improvements §3).

Each reply bug clone has had (#313, #315, #321) sat in a combination no single
scenario test enumerated: a post replying to a post while the comments leg's
context is loaded, a post replying to a comment, a comment whose parent was
lost. A generated channel + discussion group world (``tests/fakes/clone_world``)
runs every message through ``transport.decide`` and ``quotes.resolve`` and
checks three invariants on the reply each copy is sent with:

(a) it names the parent's own destination message in the parent's own chat,
    or the reply is counted as ``reply_flattened``;
(b) a comment whose thread the clone can know stays under that thread's
    destination anchor;
(c) no ``reply_to_msg_id`` or ``top_msg_id`` comes from another chat's id
    space: each names a message that exists in the chat it is sent to.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from telethon.tl import types

from tests.fakes import clone_world
from tests.fakes.clone_world import World
from tgcli.clone import legs, quotes, transport

_CHATS = {
    clone_world.DESTINATION_CHANNEL: "channel",
    clone_world.DESTINATION_GROUP: "group",
}


def _target(plan, sent_to: str) -> tuple[str, int] | None:
    """The (chat, id) a sent reply points at; a peer-less reply stays local."""
    reply_to = plan.reply_to
    if reply_to is None:
        return None
    assert isinstance(reply_to, types.InputReplyToMessage)
    peer = reply_to.reply_to_peer_id
    chat = sent_to if peer is None else _CHATS.get(getattr(peer, "channel_id", None))
    assert chat is not None, f"reply names a foreign peer {peer!r}"
    return (chat, reply_to.reply_to_msg_id)


async def _place(message, leg, source, ctx, world: World):
    cursor = {
        "posts_cursor": world.posts_cursor,
        "posts_exhausted": world.posts_exhausted,
    }
    plan = transport.decide([message], leg, source, **cursor)
    if plan.mode == "deferred":
        return plan
    return await quotes.resolve([message], plan, leg, source, ctx, **cursor)


def _check(world: World, label: str, plan, *, sent_to: str, expected, thread):
    if plan.mode == "deferred":
        # Not sent this window; it is decided again once the parent may exist.
        return
    target = _target(plan, sent_to)
    top = getattr(plan.reply_to, "top_msg_id", None)

    # (c) Every id sent exists in the chat it is sent to.
    if target is not None:
        chat, msg_id = target
        assert msg_id in world.destination_ids(chat), (
            f"(c) {label}: reply_to_msg_id={msg_id} is not a message of the "
            f"destination {chat}"
        )
    if top is not None:
        assert target is not None and target[0] == "group", (
            f"(c) {label}: top_msg_id={top} outside the destination group"
        )
        assert top in world.destination_ids("group"), (
            f"(c) {label}: top_msg_id={top} is not a destination group message"
        )

    # (a) Same chat and message as the parent, or counted as flattened.
    if target is None:
        assert plan.reply_flattened, f"(a) {label}: reply dropped, not flattened"
    elif target != expected:
        assert plan.reply_flattened and target == thread, (
            f"(a) {label}: reply sent to {target}, parent lives at {expected}"
        )

    # (b) A comment with a known thread stays under that thread's anchor.
    if thread is not None:
        assert target is not None and target[0] == "group", (
            f"(b) {label}: comment left its thread {thread}"
        )
        assert thread[1] in (target[1], top), (
            f"(b) {label}: comment sent to {target} top={top}, thread is {thread}"
        )


async def _run(world: World) -> None:
    clone_state, client = clone_world.build(world)
    posts_leg = legs.posts(clone_state)
    discussion_leg = legs.discussion(clone_state)
    for post in world.posts:
        if post.reply is None:
            continue
        ctx = clone_world.resolve_context(
            client, comments_leg_ran=world.comments_leg_ran
        )
        plan = await _place(
            clone_world.post_message(post), posts_leg, client.source, ctx, world
        )
        _check(
            world,
            f"post {post.id} -> {post.reply}",
            plan,
            sent_to="channel",
            expected=world.parent_destination(post.reply, leg="posts"),
            thread=None,
        )
    for comment in world.comments:
        # The comments leg always loads the groups before its first batch.
        ctx = clone_world.resolve_context(client, comments_leg_ran=True)
        plan = await _place(
            clone_world.comment_message(world, comment),
            discussion_leg,
            client.source_group,
            ctx,
            world,
        )
        thread_post = world.post(comment.thread)
        assert thread_post is not None
        _check(
            world,
            f"comment {comment.id} -> {comment.reply}",
            plan,
            sent_to="group",
            expected=world.parent_destination(comment.reply, leg="discussion"),
            thread=world.thread_destination(thread_post),
        )


@settings(max_examples=200, derandomize=True, deadline=None)
@given(clone_world.worlds())
def test_every_clone_reply_lands_with_its_parent_or_is_counted_flat(world):
    asyncio.run(_run(world))
