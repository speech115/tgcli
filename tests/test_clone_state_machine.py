"""Clone state across saves and crashes (ADR-0017/0060).

The machines drive ``CloneState`` the way successive `clone sync` runs do:
record copies, save now and then, and sometimes die between saves. A model of
plain dicts says what the database must hold. After every step:

- a fresh ``load`` equals what the last ``save`` was given, nothing newer;
- a state that was never saved has no database at all;
- the live object still holds everything recorded on it, saved or not;
- a remembered flood (ADR-0126) is saved with the rest and survives a crash,
  and reads as over exactly when its wait has passed.
"""

from __future__ import annotations

import copy
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tgcli.clone import state

SOURCES = st.integers(min_value=1, max_value=12)


class _Machine(RuleBasedStateMachine):
    """Shared rules. ``maps`` holds the model of every map by attribute name."""

    kind = "broadcast"

    def __init__(self):
        super().__init__()
        self.patch = pytest.MonkeyPatch()
        self.directory = tempfile.TemporaryDirectory()
        self.patch.setenv("TGCLI_STATE_DIR", self.directory.name)
        self.epoch = datetime(2026, 11, 14, tzinfo=UTC)
        self.clock = 0
        self.patch.setattr(
            state, "_now", lambda: self.epoch + timedelta(seconds=self.clock)
        )
        self.state = self._new_state()
        self.clone_id = self.state.clone_id
        self.live = self._empty()
        self.saved: dict | None = None
        self.counter = 1000

    def _new_state(self) -> state.CloneState:
        return state.CloneState.new(
            account_user_id=1,
            source_peer_id=2,
            source_title="S",
            source_kind=self.kind,
        )

    def _empty(self) -> dict:
        return {
            "id_map": {},
            "discussion_id_map": {},
            "topic_map": {},
            "avatar_photo_ids": {},
            "anchor_map": {},
            "cursor": 0,
            "discussion_cursor": 0,
            "retry_not_before": None,
        }

    def _fresh_dest(self) -> int:
        self.counter += 1
        return self.counter

    def _view(self, held: state.CloneState) -> dict:
        return {
            "id_map": dict(held.id_map),
            "discussion_id_map": dict(held.discussion_id_map),
            "topic_map": dict(held.topic_map),
            "avatar_photo_ids": dict(held.avatar_photo_ids),
            "anchor_map": dict(held.anchor_map),
            "cursor": held.cursor,
            "discussion_cursor": held.discussion_cursor,
            "retry_not_before": held.retry_not_before,
        }

    @rule(source=SOURCES)
    def copy_post(self, source):
        dest = self._fresh_dest()
        self.state.record_mapping(source, dest)
        self.state.cursor += 1
        self.live["id_map"][source] = dest
        self.live["cursor"] += 1

    @rule(peer=st.integers(min_value=1, max_value=4))
    def copy_avatar(self, peer):
        photo = self._fresh_dest()
        self.state.record_avatar(peer, photo)
        self.live["avatar_photo_ids"][peer] = photo

    @rule(seconds=st.integers(min_value=1, max_value=900))
    def flood(self, seconds):
        self.state.remember_flood(seconds)
        until = self.epoch + timedelta(seconds=self.clock + seconds)
        self.live["retry_not_before"] = until.isoformat()

    @rule()
    def complete_a_run(self):
        self.state.retry_not_before = None
        self.live["retry_not_before"] = None

    @rule(seconds=st.integers(min_value=1, max_value=900))
    def wait(self, seconds):
        self.clock += seconds

    @invariant()
    def a_flood_is_left_until_its_wait_is_over(self):
        marker = self.live["retry_not_before"]
        if marker is None:
            assert self.state.flood_left() is None
            return
        left = (datetime.fromisoformat(marker) - self.epoch).total_seconds()
        left -= self.clock
        assert self.state.flood_left() == (int(left) if left > 0 else None)

    @rule()
    def save(self):
        state.save(self.state)
        self.saved = copy.deepcopy(self.live)

    @rule()
    def crash(self):
        """The process dies: unsaved records go with it, and the next run
        starts from whatever is on disk."""
        before = self.live
        loaded = state.load(self.clone_id)
        if loaded is None:
            assert self.saved is None
            self.state = self._new_state()
            self.live = self._empty()
        else:
            assert self.saved is not None
            for table, rows in before.items():
                if isinstance(rows, dict):
                    for source in set(rows) - set(self.saved[table]):
                        assert source not in self._view(loaded)[table], (
                            f"unsaved {table} {source} survived the crash"
                        )
            self.state = loaded
            self.live = copy.deepcopy(self.saved)

    @invariant()
    def disk_holds_the_last_save(self):
        loaded = state.load(self.clone_id)
        if self.saved is None:
            assert loaded is None
        else:
            assert loaded is not None
            assert self._view(loaded) == self.saved

    @invariant()
    def memory_holds_every_record(self):
        assert self._view(self.state) == self.live

    def teardown(self):
        self.patch.undo()
        self.directory.cleanup()


class BroadcastMachine(_Machine):
    """A broadcast clone copying comments: discussion map, anchors, cursors."""

    def _new_state(self) -> state.CloneState:
        fresh = super()._new_state()
        fresh.comments = state.Comments.ENABLED
        fresh.discussion_source_peer_id = 55
        fresh.discussion_destination_peer_id = 66
        fresh.discussion_linked = True
        return fresh

    @rule(source=SOURCES)
    def copy_comment(self, source):
        dest = self._fresh_dest()
        self.state.record_discussion_mapping(source, dest)
        self.state.discussion_cursor += 1
        self.live["discussion_id_map"][source] = dest
        self.live["discussion_cursor"] += 1

    @rule(source=SOURCES, dest=st.integers(min_value=1, max_value=3))
    def remember_anchor(self, source, dest):
        self.state.record_anchor(source, dest)
        self.live["anchor_map"][source] = dest

    @rule()
    def drop_comment_progress(self):
        self.state.clear_discussion_progress()
        self.live["discussion_id_map"] = {}
        self.live["discussion_cursor"] = 0


class ForumMachine(_Machine):
    kind = "forum"

    @rule(source=st.integers(min_value=2, max_value=12))
    def create_topic(self, source):
        dest = self._fresh_dest()
        self.state.record_topic(source, dest)
        self.live["topic_map"][source] = dest

    @precondition(lambda self: bool(self.live["topic_map"]))
    @rule(source=st.integers(min_value=2, max_value=12))
    def refuse_a_reused_topic_destination(self, source):
        taken = next(iter(self.live["topic_map"].items()))
        if source == taken[0]:
            return
        with pytest.raises(ValueError, match="invalid topic mapping"):
            self.state.record_topic(source, taken[1])


for machine in (BroadcastMachine, ForumMachine):
    machine.TestCase.settings = settings(
        max_examples=100, stateful_step_count=15, derandomize=True, deadline=None
    )

TestBroadcastMachine = BroadcastMachine.TestCase
TestForumMachine = ForumMachine.TestCase
