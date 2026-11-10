"""The archive account's stored gaps across sync passes and rebaselines.

``GapsMachine`` writes passes through ``write_account_sync`` the way
`archive sync` does: each pass appends every gap it hit, possibly several
and possibly ones already stored, and `rebaseline` clears them. After every
step the stored list read back by ``read_account_sync``:

- holds every gap written since the last clear, each once;
- keeps them in the order of their latest report, so ``gap`` is the most
  recent;
- is empty right after a clear.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tgcli.archive import gaps as gaps_mod, store as store_mod

SCOPES = ("common", -1001111, -1002222, -1003333)
REASONS = ("differenceTooLong", "channelDifferenceTooLong", "channelUnavailable")

GAPS = st.builds(
    lambda scope, reason: gaps_mod.gap(scope, reason=reason),
    st.sampled_from(SCOPES),
    st.sampled_from(REASONS),
)


class GapsMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.directory = tempfile.TemporaryDirectory()
        self.conn = store_mod.connect(Path(self.directory.name) / "archive.db")
        self.model: list[dict] = []

    @rule(gaps=st.lists(GAPS, max_size=4))
    def sync_pass(self, gaps):
        store_mod.write_account_sync(self.conn, gaps=gaps, touch_sync=True)
        for gap in gaps:
            self.model = [old for old in self.model if old != gap] + [gap]

    @rule()
    def rebaseline(self):
        store_mod.write_account_sync(self.conn, clear_gap=True)
        self.model = []

    @invariant()
    def stored_gaps_match_every_report_since_the_last_clear(self):
        account = store_mod.read_account_sync(self.conn)
        assert account["gaps"] == self.model
        assert account["gap"] == (self.model[-1] if self.model else None)

    def teardown(self):
        self.conn.close()
        self.directory.cleanup()


GapsMachine.TestCase.settings = settings(
    max_examples=150, stateful_step_count=10, derandomize=True, deadline=None
)
TestGapsMachine = GapsMachine.TestCase
