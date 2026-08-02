"""The self-verifying probe (ADR-0072 decision 1, plan phase 3).

Covers #139's matrix rows G6, G7, G8 and C2, plus the phase plan's own
window and trap rows: refusal before 50% elapsed, no re-probe within one
invocation, and an unparseable ``armed_at`` refusing rather than probing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from telethon import errors as telethon_errors
from telethon.tl.functions import messages

from tgcli.errors import RateLimitError
from tgcli.governor import gate, probe
from tgcli.governor.ledger import Ledger

ACCOUNT = 7091037467
HISTORY_KEY = "messages.GetHistoryRequest"


def history(peer="peer-a"):
    return messages.GetHistoryRequest(
        peer=peer,
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=100,
        max_id=0,
        min_id=0,
        hash=0,
    )


class FakeClient:
    """Models the two things the seam depends on: `_call` and `_self_id`."""

    def __init__(self, *, self_id=ACCOUNT, raises=None):
        self._self_id = self_id
        self.sent = []
        self._raises = raises

    async def _call(self, sender, request, *args, **kwargs):
        self.sent.append(request)
        if self._raises is not None:
            raise self._raises
        return "result"


@pytest.fixture
def ledger(tmp_path):
    with Ledger.open(tmp_path / "governor.db") as store:
        yield store


def arm_elapsed(ledger, *, key=HISTORY_KEY, wait_s=3600, fraction=0.6):
    """Arm a cooldown whose recorded wait is already `fraction` elapsed.

    `fraction=0.6` deliberately sits just past the 50% window so a probe
    is due without depending on clock jitter.
    """
    now = datetime.now(UTC)
    armed_at = now - timedelta(seconds=wait_s * fraction)
    deadline = now + timedelta(seconds=wait_s * (1 - fraction))
    ledger.arm_cooldown(ACCOUNT, key, deadline, now=armed_at)


async def test_a_successful_probe_clears_the_record_and_proceeds(ledger):
    """G6: probe succeeds -> record cleared, one RPC, the original request."""
    client = FakeClient()
    gate.install(client, ledger)
    arm_elapsed(ledger)

    result = await client._call(None, history())

    assert result == "result"
    assert len(client.sent) == 1
    assert isinstance(client.sent[0], messages.GetHistoryRequest)
    assert ledger.cooldown_deadline(ACCOUNT, HISTORY_KEY) is None


async def test_a_failed_probe_rewrites_the_deadline_from_the_server(ledger):
    """G7: the fresh `retry_after` replaces the stale recorded one."""
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 300
    client = FakeClient(raises=error)
    gate.install(client, ledger)
    arm_elapsed(ledger)
    before = datetime.now(UTC)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    deadline = ledger.cooldown_deadline(ACCOUNT, HISTORY_KEY)
    assert deadline is not None
    assert timedelta(seconds=299) <= deadline - before <= timedelta(seconds=302)
    # A fresh deadline earns a fresh probe (ADR-0072 decision 1).
    assert ledger.probe_spent(ACCOUNT, HISTORY_KEY) is False


async def test_two_connections_only_one_probe_wins_the_loser_refuses(tmp_path):
    """G8: the atomic spend decides; the loser refuses with zero RPCs."""
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first, Ledger.open(path) as second:
        arm_elapsed(first)

        assert probe.claim_if_due(first, ACCOUNT, HISTORY_KEY) is True
        assert probe.claim_if_due(second, ACCOUNT, HISTORY_KEY) is False

        client = FakeClient()
        gate.install(client, second)
        with pytest.raises(RateLimitError):
            await client._call(None, history())
        assert client.sent == []


async def test_a_crash_between_claim_and_send_leaves_the_probe_spent(tmp_path):
    """C2: the spend is write-ahead, so the next invocation does not re-probe."""
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first:
        arm_elapsed(first)
        assert probe.claim_if_due(first, ACCOUNT, HISTORY_KEY) is True
        # The process dies here; no request is ever sent.

    with Ledger.open(path) as second:
        client = FakeClient()
        gate.install(client, second)
        with pytest.raises(RateLimitError):
            await client._call(None, history())
        assert client.sent == []
        assert second.probe_spent(ACCOUNT, HISTORY_KEY) is True


async def test_before_half_the_wait_elapsed_it_refuses_without_spending(ledger):
    now = datetime.now(UTC)
    ledger.arm_cooldown(ACCOUNT, HISTORY_KEY, now + timedelta(hours=2), now=now)

    client = FakeClient()
    gate.install(client, ledger)
    with pytest.raises(RateLimitError):
        await client._call(None, history())

    assert client.sent == []
    assert ledger.probe_spent(ACCOUNT, HISTORY_KEY) is False


async def test_a_failed_probe_is_not_retried_within_the_same_call(ledger):
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    client = FakeClient(raises=error)
    gate.install(client, ledger)
    arm_elapsed(ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    # The record re-armed with the server's deadline; the next request in
    # the same invocation refuses rather than probing a second time.
    with pytest.raises(RateLimitError):
        await client._call(None, history())

    assert len(client.sent) == 1


async def test_an_unparseable_armed_at_refuses_rather_than_probing(ledger):
    ledger.arm_cooldown(ACCOUNT, HISTORY_KEY, datetime.now(UTC) + timedelta(hours=1))
    ledger._db.execute("UPDATE cooldowns SET armed_at = 'not-a-date'")
    ledger._db.commit()

    client = FakeClient()
    gate.install(client, ledger)
    with pytest.raises(RateLimitError):
        await client._call(None, history())

    assert client.sent == []
