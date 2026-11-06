"""Governor ledger (ADR-0072 decision 4, plan phase 1).

Covers #139's matrix rows L1–L4 and the crash-safety halves of C2 and C5 that
belong to the store rather than to the seam.
"""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tgcli.governor.ledger import (
    BUSY_TIMEOUT_MS,
    MAX_COOLDOWN_S,
    Ledger,
)

ACCOUNT = 7091037467
HISTORY = "messages.GetHistoryRequest"
DIALOGS = "messages.GetDialogsRequest"


@pytest.fixture
def ledger(tmp_path):
    with Ledger.open(tmp_path / "governor.db") as store:
        yield store


def test_a_cooldown_row_round_trips_per_request_type(ledger):
    """L1: one row per request type, not one per account."""
    now = datetime.now(UTC)
    deadline = now + timedelta(hours=3)

    ledger.arm_cooldown(ACCOUNT, HISTORY, deadline, now=now)

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY, now=now) == deadline
    assert ledger.cooldown_deadline(ACCOUNT, DIALOGS, now=now) is None


def test_a_cooldown_is_account_scoped(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=1), now=now)

    assert ledger.cooldown_deadline(8006834784, HISTORY, now=now) is None


def test_an_expired_cooldown_reads_as_absent(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now - timedelta(seconds=1), now=now)

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY, now=now) is None


def test_an_absurd_deadline_is_clamped_at_read_time(ledger):
    """L4: a clock that ran ahead must not wedge the type forever."""
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(days=400), now=now)

    deadline = ledger.cooldown_deadline(ACCOUNT, HISTORY, now=now)

    assert deadline == now + timedelta(seconds=MAX_COOLDOWN_S)


def test_arming_stores_the_raw_value_and_only_reading_degrades_it(ledger):
    """The clamp is a read-time repair, not a rewrite of what Telegram said."""
    now = datetime.now(UTC)
    absurd = now + timedelta(days=400)
    ledger.arm_cooldown(ACCOUNT, HISTORY, absurd, now=now)

    stored = ledger._db.execute("SELECT deadline FROM cooldowns").fetchone()[0]

    assert datetime.fromisoformat(stored) == absurd


def test_a_missing_ledger_file_fails_open(tmp_path):
    """L3: absent state reads as 'nothing is cooling', never as an error."""
    with Ledger.open(tmp_path / "nested" / "governor.db") as store:
        assert store.cooldown_deadline(ACCOUNT, HISTORY) is None


def test_a_corrupt_ledger_fails_open(tmp_path):
    """L3: garbage bytes must not wedge every command on the account."""
    path = tmp_path / "governor.db"
    path.write_bytes(b"this is not a database")

    with Ledger.open(path) as store:
        assert store.degraded is True
        assert "not a database" in store.degraded_reason
        assert store.cooldown_deadline(ACCOUNT, HISTORY) is None


def test_a_degraded_ledger_still_accepts_writes_without_raising(tmp_path):
    """Losing protection is survivable; aborting the user's command is not."""
    path = tmp_path / "governor.db"
    path.write_bytes(b"this is not a database")

    with Ledger.open(path) as store:
        assert store.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC)) is True


def test_a_healthy_ledger_is_not_degraded(ledger):
    assert ledger.degraded is False
    assert ledger.degraded_reason is None


def test_arm_cooldown_sqlite_failure_reports_false(ledger):
    """A real sqlite3.Error path answers False instead of raising."""
    ledger._db.close()

    assert ledger.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC)) is False


def test_a_corrupt_row_fails_open(ledger):
    ledger.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC) + timedelta(hours=1))
    ledger._db.execute("UPDATE cooldowns SET deadline = 'not-a-date'")
    ledger._db.commit()

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY) is None


def test_a_naive_deadline_is_rejected_rather_than_guessed(ledger):
    ledger.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC) + timedelta(hours=1))
    ledger._db.execute("UPDATE cooldowns SET deadline = '2099-01-01T00:00:00'")
    ledger._db.commit()

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY) is None


@pytest.mark.parametrize("column", ["deadline", "armed_at"])
def test_a_naive_timestamp_ends_no_slow_start(ledger, column):
    """Either timestamp naive: no slow start, and no naive/aware TypeError."""
    ledger.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC) + timedelta(hours=1))
    ledger._db.execute(f"UPDATE cooldowns SET {column} = '2099-01-01T00:00:00'")
    ledger._db.commit()

    since = datetime.now(UTC) - timedelta(hours=1)
    assert ledger.cooled_since(ACCOUNT, HISTORY, since) is False


def test_active_cooldowns_lists_only_live_types(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=1), now=now)
    ledger.arm_cooldown(ACCOUNT, DIALOGS, now - timedelta(hours=1), now=now)

    assert list(ledger.active_cooldowns(ACCOUNT, now=now)) == [HISTORY]


def test_the_busy_timeout_is_set_deliberately(ledger):
    """C3: #137 found no pragma set anywhere; this one is chosen, not default."""
    assert ledger._db.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS


def test_no_record_refuses_past_its_cap_after_arming(ledger):
    """F2: the cap counts from arming. Read a day and a minute later, a
    400-day deadline has ended, as the slow-start reader already says."""
    armed = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, armed + timedelta(days=400), now=armed)
    later = armed + timedelta(seconds=MAX_COOLDOWN_S + 60)

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY, now=later) is None
    assert not ledger.cooled_since(ACCOUNT, HISTORY, later)


class LedgerMachine(RuleBasedStateMachine):
    """STATE-MACHINES §7 over the public ledger API, with a fake clock.

    Model: the latest arm wins, and a row ends at
    `min(deadline, armed_at + MAX_COOLDOWN_S)`. Both readers must agree with
    that end at every moment (G3, F2).
    """

    def __init__(self):
        super().__init__()
        self.directory = tempfile.TemporaryDirectory()
        self.ledger = Ledger.open(Path(self.directory.name) / "governor.db")
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.end: datetime | None = None

    @rule(seconds=st.integers(min_value=1, max_value=4 * MAX_COOLDOWN_S))
    def arm(self, seconds):
        deadline = self.now + timedelta(seconds=seconds)
        assert self.ledger.arm_cooldown(ACCOUNT, HISTORY, deadline, now=self.now)
        self.end = min(deadline, self.now + timedelta(seconds=MAX_COOLDOWN_S))

    @rule(seconds=st.integers(min_value=0, max_value=2 * MAX_COOLDOWN_S))
    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    @invariant()
    def readers_agree_with_the_model(self):
        cooling = self.end is not None and self.end > self.now
        deadline = self.ledger.cooldown_deadline(ACCOUNT, HISTORY, now=self.now)
        assert deadline == (self.end if cooling else None)
        if self.end is not None:
            assert self.ledger.cooled_since(ACCOUNT, HISTORY, self.end)
            assert not self.ledger.cooled_since(
                ACCOUNT, HISTORY, self.end + timedelta(microseconds=1)
            )

    def teardown(self):
        self.ledger.close()
        self.directory.cleanup()


LedgerMachine.TestCase.settings = settings(
    max_examples=100, stateful_step_count=20, derandomize=True, deadline=None
)
TestLedgerMachine = LedgerMachine.TestCase
