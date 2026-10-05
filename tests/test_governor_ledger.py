"""Governor ledger (ADR-0072 decision 4, plan phase 1).

Covers #139's matrix rows L1–L4 and the crash-safety halves of C2 and C5 that
belong to the store rather than to the seam.
"""

from datetime import UTC, datetime, timedelta

import pytest

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
        assert store.cooldown_deadline(ACCOUNT, HISTORY) is None


def test_a_degraded_ledger_still_accepts_writes_without_raising(tmp_path):
    """Losing protection is survivable; aborting the user's command is not."""
    path = tmp_path / "governor.db"
    path.write_bytes(b"this is not a database")

    with Ledger.open(path) as store:
        assert store.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC)) is True


def test_a_healthy_ledger_is_not_degraded(ledger):
    assert ledger.degraded is False


def test_arm_cooldown_sqlite_failure_keeps_a_sticky_deadline(ledger):
    """ADR-0090: a real sqlite3.Error path remembers the deadline in-process."""
    deadline = datetime.now(UTC) + timedelta(hours=1)
    ledger._db.close()

    assert ledger.arm_cooldown(ACCOUNT, HISTORY, deadline) is False
    sticky = ledger.cooldown_deadline(ACCOUNT, HISTORY)
    assert sticky is not None
    assert sticky <= deadline + timedelta(seconds=1)


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


def test_active_cooldowns_lists_only_live_types(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=1), now=now)
    ledger.arm_cooldown(ACCOUNT, DIALOGS, now - timedelta(hours=1), now=now)

    assert list(ledger.active_cooldowns(ACCOUNT, now=now)) == [HISTORY]


def test_the_busy_timeout_is_set_deliberately(ledger):
    """C3: #137 found no pragma set anywhere; this one is chosen, not default."""
    assert ledger._db.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
