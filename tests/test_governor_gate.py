"""The governed `_call` seam (ADR-0072 decision 2, plan phase 2).

Covers #139's matrix rows G1, G2, G9, S5 and S7.
"""

from datetime import UTC, datetime, timedelta

import pytest
from telethon import errors as telethon_errors
from telethon.tl.functions import messages, upload

from tgcli.errors import RateLimitError
from tgcli.governor import gate
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


def arm(ledger, key=HISTORY_KEY, seconds=3600):
    ledger.arm_cooldown(ACCOUNT, key, datetime.now(UTC) + timedelta(seconds=seconds))


async def test_a_cooling_request_type_refuses_before_any_rpc(ledger):
    """G9: zero RPCs while cooling — the refusal is local, not a round trip."""
    client = FakeClient()
    gate.install(client, ledger)
    arm(ledger)

    with pytest.raises(RateLimitError) as caught:
        await client._call(None, history())

    assert client.sent == []
    assert caught.value.details["retry_after"] > 0
    assert caught.value.code == "FLOOD_WAIT"
    assert caught.value.exit_code == 5


async def test_an_unrelated_request_type_proceeds(ledger):
    """G1: the cooldown is per request type, not account-wide paralysis."""
    client = FakeClient()
    gate.install(client, ledger)
    arm(ledger)

    assert await client._call(None, messages.SendMessageRequest("p", "hi")) == "result"
    assert len(client.sent) == 1


async def test_the_peer_is_not_part_of_the_key(ledger):
    """G2: a flood reading peer A stops reads of peer B too."""
    client = FakeClient(raises=telethon_errors.FloodWaitError(request=None))
    client._raises.seconds = 600
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history(peer="peer-a"))

    clean = FakeClient()
    gate.install(clean, ledger)
    with pytest.raises(RateLimitError):
        await clean._call(None, history(peer="peer-b"))
    assert clean.sent == []


async def test_a_flood_arms_the_cooldown_from_the_server_deadline(ledger):
    """S7: the wrapper sees the type before send and the outcome after."""
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 1234
    client = FakeClient(raises=error)
    gate.install(client, ledger)
    before = datetime.now(UTC)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    deadline = ledger.cooldown_deadline(ACCOUNT, HISTORY_KEY)
    assert deadline is not None
    assert timedelta(seconds=1233) <= deadline - before <= timedelta(seconds=1236)


async def test_a_flood_on_one_type_leaves_the_others_free(ledger):
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY_KEY) is not None
    assert ledger.cooldown_deadline(ACCOUNT, "messages.SendMessageRequest") is None


async def test_a_direct_call_from_the_download_path_is_governed(ledger):
    """S5: the reason `__call__` was rejected as the seam.

    `downloads.py` calls `_call` itself on a per-datacentre sender. An
    instance attribute still shadows the class method for those calls.
    """
    client = FakeClient()
    gate.install(client, ledger)
    arm(ledger, key="upload.GetFileRequest")

    with pytest.raises(RateLimitError):
        await client._call(
            "other-dc-sender", upload.GetFileRequest(location=None, offset=0, limit=1)
        )

    assert client.sent == []


async def test_an_expired_cooldown_stops_refusing(ledger):
    client = FakeClient()
    gate.install(client, ledger)
    ledger.arm_cooldown(ACCOUNT, HISTORY_KEY, datetime.now(UTC) - timedelta(seconds=1))

    assert await client._call(None, history()) == "result"


async def test_traffic_before_the_session_knows_its_account_is_not_gated(ledger):
    """Connection and authorization must work on a cooling account."""
    client = FakeClient(self_id=None)
    gate.install(client, ledger)
    arm(ledger)

    assert await client._call(None, history()) == "result"


async def test_a_flood_without_a_usable_wait_arms_nothing(ledger):
    """A malformed error must not persist a deadline nobody can justify."""
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 0
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    assert ledger.cooldown_deadline(ACCOUNT, HISTORY_KEY) is None


async def test_the_wrapper_forwards_arguments_untouched(ledger):
    seen = {}

    class Recording(FakeClient):
        async def _call(self, sender, request, *args, **kwargs):
            seen["sender"] = sender
            seen["args"] = args
            seen["kwargs"] = kwargs
            return "ok"

    client = Recording()
    gate.install(client, ledger)

    assert await client._call("snd", history(), ordered=True) == "ok"
    assert seen["sender"] == "snd"
    assert seen["kwargs"] == {"ordered": True}
