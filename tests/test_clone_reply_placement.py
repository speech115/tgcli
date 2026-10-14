"""Property: every clone reply lands where its parent lives (tgcli-improvements §3).

Each reply bug clone has had (#313, #315, #321) sat in a combination no single
scenario test enumerated: a post replying to a post while the comments leg's
context is loaded, a post replying to a comment, a comment whose parent was
lost. A generated channel + discussion group world (``tests/fakes/clone_world``)
is seeded as an earlier run left it, then ``tg clone sync`` copies the rest.
Every send request the destination received is checked:

(a) its ``reply_to`` names the parent's own destination message in the
    parent's chat, or the reply is lost, and the lost ones add up to the
    ``reply_flattened`` count the sync reports;
(b) a comment whose thread the clone can know stays under that thread's
    destination anchor;
(c) no ``reply_to_msg_id`` or ``top_msg_id`` comes from another chat's id
    space: each names a message that exists in the chat it points into.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from telethon.tl import types

from tests.conftest import make_session_fake
from tests.fakes import clone_world
from tests.fakes.clone_env import config_env as config_env
from tests.fakes.clone_world import Reply, World
from tgcli.cli import main
from tgcli.clone import legs, state
from tgcli.governor import pacing

_CHATS = {
    clone_world.DESTINATION_CHANNEL: "channel",
    clone_world.DESTINATION_GROUP: "group",
}


class Truth:
    """Where things live in the destination once the sync has finished."""

    def __init__(self, world: World, final: state.CloneState, client):
        self.world = world
        self.final = final
        self.client = client

    def anchor_of(self, post) -> int | None:
        dest = self.final.dest_for(post.id)
        return None if dest is None else self.client.anchor_ids.get(dest)

    def thread(self, post) -> tuple[str, int] | None:
        """The destination anchor a comment under ``post`` must hang off,
        when the clone can know it: the post's copy has an anchor, and the
        source anchor is readable or an earlier run recorded the pair."""
        found = self.anchor_of(post)
        if found is None or not (post.anchor_present or post.anchor_remembered):
            return None
        return ("group", found)

    def parent(self, reply: Reply, *, leg: str) -> tuple[str, int] | None:
        """Where ``reply``'s parent lives in the destination, or None if lost."""
        if reply.space == "channel":
            post = self.world.post(reply.id)
            if post is None:
                return None
            if leg == "posts":
                dest = self.final.dest_for(post.id)
                return None if dest is None else ("channel", dest)
            # A comment quoting a channel post points at its discussion anchor.
            found = self.anchor_of(post)
            return None if found is None else ("group", found)
        if self.world.comment(reply.id) is not None:
            dest = self.final.discussion_dest_for(reply.id)
            return None if dest is None else ("group", dest)
        post = self.world.anchored(reply.id)
        return None if post is None else self.thread(post)

    def exists(self, chat: str, msg_id: int) -> bool:
        if chat == "channel":
            return msg_id in self.final.id_map.values()
        return (
            msg_id in self.final.discussion_id_map.values()
            or msg_id in self.client.anchor_ids.values()
        )


def _reply(request, chat: str):
    """The (chat, id) a sent copy replies to, and its thread top."""
    reply_to = getattr(request, "reply_to", None)
    if reply_to is None:
        return None, getattr(request, "top_msg_id", None)
    assert isinstance(reply_to, types.InputReplyToMessage)
    peer = reply_to.reply_to_peer_id
    target = chat if peer is None else _CHATS.get(getattr(peer, "channel_id", None))
    assert target is not None, f"reply names a foreign peer {peer!r}"
    return (target, reply_to.reply_to_msg_id), reply_to.top_msg_id


def _check(truth: Truth, sync: dict) -> None:
    world, final, client = truth.world, truth.final, truth.client
    copies = [
        ("posts", "channel", post, final.dest_for(post.id), None)
        for post in world.posts
        if not post.copied_before
    ] + [
        ("discussion", "group", comment, final.discussion_dest_for(comment.id), comment)
        for comment in world.comments
        if not comment.copied_before
    ]
    lost = 0
    for leg, chat, item, dest, comment in copies:
        if dest is None or (chat, dest) not in client.sent:
            continue  # not copied by this run
        label = f"{leg} {item.id} -> {item.reply}"
        target, top = _reply(client.sent[(chat, dest)], chat)
        thread = None
        if comment is not None:
            thread_post = world.post(comment.thread)
            assert thread_post is not None
            thread = truth.thread(thread_post)

        # (c) Every id sent exists in the chat it points into.
        if target is not None:
            assert truth.exists(*target), (
                f"(c) {label}: reply_to_msg_id={target[1]} is not a message of "
                f"the destination {target[0]}"
            )
        if top is not None:
            assert target is not None and target[0] == "group", (
                f"(c) {label}: top_msg_id={top} outside the destination group"
            )
            assert truth.exists("group", top), (
                f"(c) {label}: top_msg_id={top} is not a destination group message"
            )

        # (a) Same chat and message as the parent, or a counted loss.
        if item.reply is not None:
            expected = truth.parent(item.reply, leg=leg)
            if expected is None or target != expected:
                lost += 1
                assert target is None or target == thread, (
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
    assert sync["reply_flattened"] == lost, (
        f"(a) sync reports {sync['reply_flattened']} flattened replies, "
        f"{lost} copies lost their parent"
    )


@settings(
    max_examples=120,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(world=clone_world.worlds())
def test_every_clone_reply_lands_with_its_parent_or_is_counted_flat(
    world, config_env, tmp_path_factory, capsys
):
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path_factory.mktemp("state")))
        monkeypatch.setattr(legs, "WINDOW", world.window)
        pacing.reset_runtime()
        seeded, client = clone_world.build(world)
        make_session_fake(monkeypatch, client)
        capsys.readouterr()

        assert main(["clone", "sync", "@source", "--json"]) == 0

        sync = json.loads(capsys.readouterr().out)["sync"]
        final = state.load(seeded.clone_id)
        assert final is not None
        _check(Truth(world, final, client), sync)
