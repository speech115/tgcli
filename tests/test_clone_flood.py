"""Governor ledger cooldown record (ADR-0072, replacing ADR-0045's JSON).

The per-account flood record moved from one JSON file under `clones/` to
the governor's SQLite ledger (ADR-0072 decision 4, plan phase 6). These
tests pin the same behaviours the JSON record promised: roundtrip, expiry,
read-time clamping, fail-open reads, and keeping the later of two
deadlines.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tgcli.governor.ledger import MAX_COOLDOWN_S, Ledger

ACCOUNT = 42


@pytest.fixture
def ledger(tmp_path):
    with Ledger.open(tmp_path / "governor.db") as store:
        yield store


def test_arm_and_read_roundtrip(ledger):
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", deadline)

    assert ledger.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest") == deadline


def test_expired_deadline_reads_as_none(ledger):
    past = datetime.now(UTC) - timedelta(seconds=5)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", past)

    assert ledger.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest") is None


def test_absent_record_reads_as_none(ledger):
    assert ledger.cooldown_deadline(999, "messages.GetHistoryRequest") is None
    assert ledger.active_cooldowns(999) == {}


def test_a_corrupt_ledger_fails_open(tmp_path):
    path = tmp_path / "governor.db"
    path.write_bytes(b"not a database")

    with Ledger.open(path) as store:
        assert store.degraded is True
        assert store.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest") is None


def test_arm_overwrites_with_the_latest_deadline(ledger):
    """The ledger records what the server said last: latest arm wins.

    The ADR-0045 JSON record kept the later of two deadlines; the governor
    ledger deliberately does not — a fresh 420 supersedes an older one,
    matching how Telegram's own `_flood_waited_requests` behaves.
    """
    longer = datetime.now(UTC) + timedelta(minutes=30)
    shorter = datetime.now(UTC) + timedelta(minutes=5)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", longer)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", shorter)

    assert ledger.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest") == shorter


def test_absurd_future_deadline_is_clamped_on_read(ledger):
    far = datetime.now(UTC) + timedelta(days=3650)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", far)

    deadline = ledger.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest")
    assert deadline is not None
    ahead = (deadline - datetime.now(UTC)).total_seconds()
    assert 0 < ahead <= MAX_COOLDOWN_S


def test_deadline_within_max_is_not_clamped(ledger):
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", deadline)

    assert ledger.cooldown_deadline(ACCOUNT, "messages.GetHistoryRequest") == deadline


def test_active_cooldowns_lists_live_types(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, "messages.GetHistoryRequest", now + timedelta(hours=1))
    ledger.arm_cooldown(ACCOUNT, "messages.GetDialogsRequest", now - timedelta(hours=1))

    assert list(ledger.active_cooldowns(ACCOUNT)) == ["messages.GetHistoryRequest"]
