"""Fault injection: `tg clone sync` against a Telegram that fails mid-run.

STATE-MACHINES §5 (L1, L2, L3) promises that a sync cut short anywhere
resumes without losing or duplicating work. ``CloneSyncFaultMachine`` runs
the real ``tg clone sync`` over a generated channel + discussion group world
(``tests/fakes/clone_world``) and fails the n-th Telegram operation of a run:
a FloodWait, a 5xx, a timeout, a dropped connection, a killed process, or,
for a send Telegram already ran, a lost or unconfirmed answer
(``tests/fakes/clone_faults``). Between runs the owner may repair a tail the
clone refused. After every run:

- the clone state loads, and each id it maps is the copy Telegram made of
  that source message, in that chat;
- no source message has two copies in the destination;
- every message at or below a leg's cursor is mapped (L1);
- a copy the clone did not record makes every later run exit 2 before it
  copies into that chat (L2), until the owner repairs the tail;
- a run that fails exits as its fault says: 5 for a flood of either kind.

And whenever the faults stop, one clean run finishes the clone: every post
and comment copied exactly once, each copy the one a run of the same world
that never failed makes (except STATE-MACHINES F7).
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import event, settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)
from telethon.tl import types

from tests.conftest import make_session_fake
from tests.fakes import clone_faults, clone_world
from tests.fakes.clone import SAMPLE, message
from tgcli.cli import main
from tgcli.clone import legs, state
from tgcli.governor import pacing

# The n-th operation of a run, and the n-th send: a run makes a few dozen
# operations and a handful of sends, so both counters reach every step.
plans = st.dictionaries(
    st.integers(0, 30), st.sampled_from(clone_faults.KINDS), max_size=2
)
send_plans = st.dictionaries(
    st.integers(0, 6), st.sampled_from(clone_faults.KINDS), max_size=1
)


EXIT_FOR = {
    "flood": 5,
    "premium_flood": 5,
    "server": 1,
    "timeout": 1,
    "connection": 1,
    "lost": 1,
    "too_long": 2,
    "unconfirmed": 2,
    "kill": None,
    "kill_after": None,
}


def _lost_cross_leg_reply(key, expected, got) -> bool:
    """A post whose reply to a comment F7 lost: sent flat, nothing else differs.

    Without a reply to carry, a post the oracle reuploaded is forwarded as
    itself, so its copy is either that bare forward or the oracle's own body.
    """
    return (
        key[0] == "channel"
        and expected[2] is not None
        and expected[2][0] == "group"
        and got[2] is None
        and got[3] is None
        and (got[:2] == ("ForwardMessagesRequest", None) or got[:2] == expected[:2])
    )


class CloneSyncFaultMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.patch = pytest.MonkeyPatch()
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "config.toml").write_text(SAMPLE)
        self.patch.setenv("TGCLI_CONFIG", str(root / "config.toml"))
        self.patch.setenv("TGCLI_STATE_DIR", str(root / "state"))
        self.patch.delenv("TGCLI_READONLY", raising=False)
        self.repaired: set[tuple[str, int]] = set()

    def _client(self, world):
        seeded, plain = clone_world.build(world)
        self.clone_id = seeded.clone_id
        client = clone_faults.FaultyWorldClient(plain.messages, plain.comments)
        client.destination_last_id = plain.destination_last_id
        client.group_last_id = plain.group_last_id
        client.anchor_ids = plain.anchor_ids
        client.copies = dict(plain.copies)
        client.seed_created()
        make_session_fake(self.patch, client)
        return client

    @initialize(world=clone_world.worlds())
    def seed(self, world):
        self.world = world
        self.patch.setattr(legs, "WINDOW", world.window)
        root = Path(self.directory.name)
        # The oracle: the same world synced once with nothing failing.
        self.patch.setenv("TGCLI_STATE_DIR", str(root / "oracle"))
        self.client = self._client(world)
        assert self._run({}) == 0, self.err
        self.oracle = clone_faults.shape(self.client, self._recorded())
        self.patch.setenv("TGCLI_STATE_DIR", str(root / "state"))
        self.client = self._client(world)

    def _run(self, plan, sends=None) -> int | None:
        pacing.reset_runtime()
        self.client.arm(plan, sends)
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            try:
                code = main(["clone", "sync", "@source", "--json"])
            except clone_faults.Killed:
                code = None
        self.fired, self.log = self.client.fired, self.client.log
        self.client.arm({})
        self.out, self.err = out.getvalue(), err.getvalue()
        return code

    def _state(self) -> state.CloneState:
        loaded = state.load(self.clone_id)
        assert loaded is not None
        return loaded

    def _recorded(self) -> set[tuple[str, int, int]]:
        saved = self._state()
        return {
            ("channel", int(source), dest) for source, dest in saved.id_map.items()
        } | {
            ("group", int(source), dest)
            for source, dest in saved.discussion_id_map.items()
        }

    def _strays(self) -> list[tuple[str, int, int]]:
        """Copies Telegram made that the clone never recorded, not yet repaired."""
        recorded = self._recorded()
        return [
            item
            for item in self.client.created
            if item not in recorded and (item[0], item[2]) not in self.repaired
        ]

    @rule(plan=plans, sends=send_plans)
    def sync(self, plan, sends):
        strays = self._strays()
        created = len(self.client.created)
        code = self._run(plan, sends)
        fired = self.fired
        event(f"fired {sorted(set(fired))} exit {code}")
        if strays:
            # L2: a copy the clone lost track of stops the next copy into
            # that chat cold, and the run says so (exit 2, manual repair).
            chats = {chat for chat, _source, _dest in strays}
            new = self.client.created[created:]
            assert not [item for item in new if item[0] in chats], (
                f"copied into {chats} past unrecorded {strays}"
            )
            if not fired:
                assert code == 2, (code, self.err)
        elif not fired:
            assert code == 0, (code, self.err)
        if fired and code != 0:
            # The fault that ended the run names the exit (CONTRACT §4): a
            # flood of either kind is 5 with retry_after, a lost or failed
            # request 1, a send Telegram did not confirm 2.
            assert code == EXIT_FOR[fired[-1]], (fired, code, self.err)

    @precondition(lambda self: self._strays())
    @rule()
    def repair(self):
        """The owner deletes the copies the clone refused over (CONTRACT:
        'manual repair'); the fake shows a deleted id as a service message."""
        for chat, _source, dest in self._strays():
            self.repaired.add((chat, dest))
            action = types.MessageActionChatEditTitle(title="deleted")
            if chat == "channel":
                self.client.destination_actions[dest] = action
            else:
                self.client.group_tail[dest] = message(
                    dest, chat=self.client.destination_group, action=action
                )

    @rule()
    def finish(self):
        """With the faults over and the tail repaired, one run completes."""
        self.repair()
        event("finish")
        code = self._run({})
        assert code == 0, self.err
        sync = json.loads(self.out)["sync"]
        assert sync["more"] is False
        saved = self._state()
        assert {int(key) for key in saved.id_map} == {
            post.id for post in self.world.posts
        }
        assert {int(key) for key in saved.discussion_id_map} == {
            comment.id for comment in self.world.comments
        }
        # No fault leaves a lasting mark: every copy is the one a run that
        # never failed makes, down to its body and where its reply points.
        # One known exception (STATE-MACHINES F7, open): a resumed run starts
        # with a posts window, so a post replying to a comment the cut-short
        # run had not copied yet goes out flat instead of quoting it.
        got = clone_faults.shape(self.client, self._recorded())
        assert got.keys() == self.oracle.keys()
        for key, expected in self.oracle.items():
            if got[key] != expected and _lost_cross_leg_reply(key, expected, got[key]):
                continue
            assert got[key] == expected, key

    @invariant()
    def state_maps_what_telegram_made(self):
        if not hasattr(self, "client"):
            return
        made = set(self.client.created)
        for item in self._recorded():
            assert item in made, f"state maps {item}, Telegram made no such copy"

    @invariant()
    def no_source_message_is_copied_twice(self):
        if not hasattr(self, "client"):
            return
        seen: dict[tuple[str, int], int] = {}
        for chat, source, dest in self.client.created:
            if (chat, dest) in self.repaired:
                continue
            assert (chat, source) not in seen, (
                f"{chat} source {source} copied twice: {seen[(chat, source)]}, {dest}"
            )
            seen[(chat, source)] = dest

    @invariant()
    def cursors_never_pass_an_unmapped_message(self):
        if not hasattr(self, "client"):
            return
        saved = self._state()
        mapped_posts = {int(key) for key in saved.id_map}
        for post in self.world.posts:
            if post.id <= saved.cursor:
                assert post.id in mapped_posts, (
                    f"posts cursor {saved.cursor} passed unmapped post {post.id}"
                )
        mapped_comments = {int(key) for key in saved.discussion_id_map}
        for comment in self.world.comments:
            if comment.id <= saved.discussion_cursor:
                assert comment.id in mapped_comments, (
                    f"discussion cursor {saved.discussion_cursor} passed "
                    f"unmapped comment {comment.id}"
                )

    def teardown(self):
        self.patch.undo()
        self.directory.cleanup()


CloneSyncFaultMachine.TestCase.settings = settings(
    max_examples=int(__import__("os").environ.get("FUZZ_N", 200)),
    stateful_step_count=8,
    derandomize=True,
    deadline=None,
)
TestCloneSyncFaultMachine = CloneSyncFaultMachine.TestCase
