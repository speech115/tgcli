"""Governor ledger (ADR-0072 decision 4, plan phase 1).

Covers #139's matrix rows L1–L4 and the crash-safety halves of C2 and C5 that
belong to the store rather than to the seam.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tgcli.governor.ledger import (
    BREADTH_BUDGET,
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
        assert store.peers_in_window(ACCOUNT, 0.0) == 0


def test_a_corrupt_ledger_fails_open(tmp_path):
    """L3: garbage bytes must not wedge every command on the account."""
    path = tmp_path / "governor.db"
    path.write_bytes(b"this is not a database")

    with Ledger.open(path) as store:
        assert store.degraded is True
        assert store.cooldown_deadline(ACCOUNT, HISTORY) is None
        assert store.breadth_remaining(ACCOUNT, 0.0) == BREADTH_BUDGET


def test_a_degraded_ledger_still_accepts_writes_without_raising(tmp_path):
    """Losing protection is survivable; aborting the user's command is not."""
    path = tmp_path / "governor.db"
    path.write_bytes(b"this is not a database")

    with Ledger.open(path) as store:
        assert store.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC)) is True
        assert store.touch_peer(ACCOUNT, 1, 0.0) is True


def test_a_healthy_ledger_is_not_degraded(ledger):
    assert ledger.degraded is False


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


def test_the_probe_is_spendable_exactly_once(ledger):
    """C2: write-ahead spending is what makes a crash cost nothing."""
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=2), now=now)

    assert ledger.probe_spent(ACCOUNT, HISTORY) is False
    assert ledger.spend_probe(ACCOUNT, HISTORY) is True
    assert ledger.probe_spent(ACCOUNT, HISTORY) is True
    assert ledger.spend_probe(ACCOUNT, HISTORY) is False


def test_a_fresh_deadline_earns_a_fresh_probe(ledger):
    """Decision 1 bounds the probe at one request per confirmed deadline."""
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=2), now=now)
    ledger.spend_probe(ACCOUNT, HISTORY)

    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=5), now=now)

    assert ledger.probe_spent(ACCOUNT, HISTORY) is False


def test_active_cooldowns_lists_only_live_types(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=1), now=now)
    ledger.arm_cooldown(ACCOUNT, DIALOGS, now - timedelta(hours=1), now=now)

    assert list(ledger.active_cooldowns(ACCOUNT, now=now)) == [HISTORY]


def test_a_pacing_reservation_round_trips(ledger):
    ledger.reserve(ACCOUNT, HISTORY, 1000.0)

    assert ledger.last_reserved(ACCOUNT, HISTORY) == 1000.0
    assert ledger.last_reserved(ACCOUNT, DIALOGS) is None


def test_a_reservation_stamped_in_the_future_is_clamped(ledger):
    """One NTP correction must not wedge a request type (resolve_phone's bug)."""
    ledger.reserve(ACCOUNT, HISTORY, 5000.0)

    assert ledger.clamp_reservation(ACCOUNT, HISTORY, now=1000.0) == 1000.0
    assert ledger.last_reserved(ACCOUNT, HISTORY) == 1000.0


def test_a_sane_reservation_is_left_alone(ledger):
    ledger.reserve(ACCOUNT, HISTORY, 900.0)

    assert ledger.clamp_reservation(ACCOUNT, HISTORY, now=1000.0) == 900.0


def test_peer_touches_count_distinct_peers_inside_the_window(ledger):
    """L2: the budget is distinct peers per rolling window, across runs."""
    now = 1_000_000.0
    for peer in (1, 2, 3):
        ledger.touch_peer(ACCOUNT, peer, now)
    ledger.touch_peer(ACCOUNT, 4, now - 90_000)  # older than 24 h

    assert ledger.peers_in_window(ACCOUNT, now) == 3
    assert ledger.breadth_remaining(ACCOUNT, now) == BREADTH_BUDGET - 3


def test_touching_the_same_peer_twice_spends_one_unit(ledger):
    now = 1_000_000.0
    ledger.touch_peer(ACCOUNT, 1, now - 10)
    ledger.touch_peer(ACCOUNT, 1, now)

    assert ledger.peers_in_window(ACCOUNT, now) == 1


def test_a_peer_touched_again_re_enters_the_window(ledger):
    now = 1_000_000.0
    ledger.touch_peer(ACCOUNT, 1, now - 90_000)
    assert ledger.peers_in_window(ACCOUNT, now) == 0

    ledger.touch_peer(ACCOUNT, 1, now)
    assert ledger.peers_in_window(ACCOUNT, now) == 1


def test_peer_touches_survive_a_reopened_ledger(tmp_path):
    """C5: a killed process does not hand back budget it really spent."""
    path = tmp_path / "governor.db"
    now = 1_000_000.0
    with Ledger.open(path) as store:
        for peer in range(10):
            store.touch_peer(ACCOUNT, peer, now)

    with Ledger.open(path) as reopened:
        assert reopened.breadth_remaining(ACCOUNT, now) == BREADTH_BUDGET - 10


def test_the_busy_timeout_is_set_deliberately(ledger):
    """C3: #137 found no pragma set anywhere; this one is chosen, not default."""
    assert ledger._db.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS


def test_two_connections_share_one_account_clock(tmp_path):
    """C1: the budget is account-scoped, not per process."""
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first, Ledger.open(path) as second:
        first.reserve(ACCOUNT, HISTORY, 1234.0)

        assert second.last_reserved(ACCOUNT, HISTORY) == 1234.0


def test_reserve_is_atomic_against_a_fresher_competitor(tmp_path):
    """m1 review fix: two processes racing to reserve must not both win.

    The reservation is start-to-start (ADR-0072 decision 3); a conditional
    upsert means the later claim loses instead of overwriting the earlier
    one, so a pair of simultaneous processes cannot dispatch back-to-back.
    """
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first, Ledger.open(path) as second:
        # First process reads "nothing reserved" and reserves t=10.
        assert first.reserve(ACCOUNT, HISTORY, 10.0) is True
        # Second process read "nothing reserved" too, but its claim at t=5
        # is older than what landed — it must lose the race.
        assert second.reserve(ACCOUNT, HISTORY, 5.0) is False
        assert first.last_reserved(ACCOUNT, HISTORY) == 10.0
        # A genuinely later claim still wins.
        assert second.reserve(ACCOUNT, HISTORY, 20.0) is True
        assert first.last_reserved(ACCOUNT, HISTORY) == 20.0


def test_cooldown_armed_at_round_trips(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY, now + timedelta(hours=1), now=now)

    assert ledger.cooldown_armed_at(ACCOUNT, HISTORY) == now


def test_cooldown_armed_at_missing_row_reads_none(ledger):
    assert ledger.cooldown_armed_at(ACCOUNT, HISTORY) is None


def test_cooldown_armed_at_rejects_naive_timestamps(ledger):
    ledger.arm_cooldown(ACCOUNT, HISTORY, datetime.now(UTC) + timedelta(hours=1))
    ledger._db.execute("UPDATE cooldowns SET armed_at = '2026-07-15T12:00:00'")
    ledger._db.commit()

    assert ledger.cooldown_armed_at(ACCOUNT, HISTORY) is None


def test_reserve_at_the_same_moment_loses_not_overwrites(tmp_path):
    """Review blocker 3: two processes dispatching at the same instant must
    not both claim it — the loser's equal-timestamp claim is refused, and
    the winner's reservation survives untouched."""
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first, Ledger.open(path) as second:
        assert first.reserve(ACCOUNT, HISTORY, 10.0) is True
        # Identical moment: strict comparison refuses the second claim
        # instead of overwriting the first (the old <= let it through).
        assert second.reserve(ACCOUNT, HISTORY, 10.0) is False
        assert first.last_reserved(ACCOUNT, HISTORY) == 10.0
