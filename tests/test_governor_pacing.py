"""Pacing and the breadth budget (ADR-0072 decision 3, plan phase 4).

Covers #139's matrix rows P1-P9, C1 and C5. No test sleeps in real time:
a fake clock and a recording sleep function stand in for both.
"""

import time

import pytest
from telethon.tl.functions import messages, updates, upload

from tgcli.archive import store as store_mod
from tgcli.governor import gate, pacing, registry
from tgcli.governor.ledger import Ledger

ACCOUNT = 7091037467
HISTORY_KEY = "messages.GetHistoryRequest"

_SAMPLE_CONFIG = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(_SAMPLE_CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / "sessions" / "main.session").write_bytes(b"x")
    return state


def history(*, limit=100, peer="peer-a"):
    return messages.GetHistoryRequest(
        peer=peer,
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=limit,
        max_id=0,
        min_id=0,
        hash=0,
    )


def dialogs_page():
    return messages.GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer="p", limit=100, hash=0
    )


class FakeClient:
    """Models `_call` with a configurable request latency on a fake clock."""

    def __init__(self, *, self_id=ACCOUNT, latency=0.0, sleep=None, clock=None):
        self._self_id = self_id
        self.latency = latency
        self.sleep = sleep
        self.clock = clock
        self.dispatched_at = []
        self.sent = []

    async def _call(self, sender, request, *args, **kwargs):
        self.dispatched_at.append(self.clock["t"])
        self.sent.append(request)
        if self.latency:
            await self.sleep(self.latency)
        return "result"


@pytest.fixture
def clock():
    return {"t": 0.0}


@pytest.fixture
def sleeper(clock):
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        clock["t"] += seconds

    fake_sleep.calls = calls
    return fake_sleep


@pytest.fixture
def ledger(tmp_path):
    with Ledger.open(tmp_path / "governor.db") as store:
        yield store


@pytest.fixture
def client(ledger, clock, sleeper):
    fake = FakeClient(clock=clock, sleep=sleeper)
    gate.install(fake, ledger, sleep=sleeper, clock=lambda: clock["t"])
    return fake


async def test_two_history_reads_sleep_to_the_three_second_floor(client, sleeper):
    """P1: the second read waits out the rest of the interval."""
    await client._call(None, history())
    await client._call(None, history())

    assert sleeper.calls == [3.0]


async def test_pacing_is_start_to_start_not_end_to_start(client, sleeper, clock):
    """P1b: a 2 s request must not stretch a 3 s interval into 5 s."""
    slow = FakeClient(latency=2.0, clock=clock, sleep=sleeper)
    gate.install(slow, client._tgcli_governor, sleep=sleeper, clock=lambda: clock["t"])

    await slow._call(None, history())
    await slow._call(None, history())

    assert slow.dispatched_at == [0.0, 3.0]
    assert sleeper.calls == [2.0, 1.0, 2.0]


async def test_get_messages_by_id_owes_one_ten_second_gap_not_two(client, sleeper):
    """P2: 600 ids are one 10 s gap, chunked at 300, never two gaps."""
    big = messages.GetMessagesRequest(id=list(range(600)))

    await client._call(None, big)
    await client._call(None, big)

    assert sleeper.calls == [10.0]


async def test_media_paces_per_file_not_per_chunk(client, sleeper):
    """P3: three files owe 3 s between files; continuation chunks owe nothing."""
    file_one_chunks = [
        upload.GetFileRequest(location=None, offset=0, limit=100),
        upload.GetFileRequest(location=None, offset=100, limit=100),
        upload.GetFileRequest(location=None, offset=200, limit=100),
    ]
    file_two = upload.GetFileRequest(location=None, offset=0, limit=100)
    file_three = upload.GetFileRequest(location=None, offset=0, limit=100)

    for chunk in file_one_chunks + [file_two, file_three]:
        await client._call(None, chunk)

    assert sleeper.calls == [3.0, 3.0]


async def test_dialog_pages_are_paced_three_seconds(client, sleeper):
    """P4: two dialog enumeration pages owe one 3 s gap."""
    await client._call(None, dialogs_page())
    await client._call(None, dialogs_page())

    assert sleeper.calls == [3.0]


async def test_mutations_are_not_paced(client, sleeper):
    """P5: two sends back to back owe no sleep."""
    send = messages.SendMessageRequest("peer-a", "hi")

    await client._call(None, send)
    await client._call(None, send)

    assert sleeper.calls == []


async def test_unlisted_request_types_are_not_paced(client, sleeper):
    """An unlisted type has no interval: the cooldown still gates it."""
    await client._call(None, history())
    await client._call(None, updates.GetStateRequest())
    await client._call(None, history())

    assert sleeper.calls == [3.0]  # only the second history read paced


def test_a_run_with_95_peers_in_window_can_touch_five_more(ledger):
    """P7: the budget is exhausted only once 100 distinct peers are touched."""
    now = 1_000_000.0
    for peer in range(95):
        ledger.touch_peer(ACCOUNT, peer, now)

    assert pacing.budget_ok(ledger, ACCOUNT, now=now) is True
    for peer in range(95, 100):
        ledger.touch_peer(ACCOUNT, peer, now)
    assert pacing.budget_ok(ledger, ACCOUNT, now=now) is False


def test_killed_after_ten_peers_leaves_ninety_budget(tmp_path):
    """C5: peer touches survive a reopened ledger; the run did the reading."""
    now = 1_000_000.0
    path = tmp_path / "governor.db"
    with Ledger.open(path) as store:
        for peer in range(10):
            store.touch_peer(ACCOUNT, peer, now)

    with Ledger.open(path) as reopened:
        assert reopened.breadth_remaining(ACCOUNT, now) == 90


async def test_two_ledger_connections_share_one_pacing_clock(tmp_path, clock, sleeper):
    """C1: the reservation is account-scoped, not per process."""
    path = tmp_path / "governor.db"
    with Ledger.open(path) as first, Ledger.open(path) as second:
        client_a = FakeClient(clock=clock, sleep=sleeper)
        client_b = FakeClient(clock=clock, sleep=sleeper)
        gate.install(client_a, first, sleep=sleeper, clock=lambda: clock["t"])
        gate.install(client_b, second, sleep=sleeper, clock=lambda: clock["t"])

        await client_a._call(None, history())
        await client_b._call(None, history())

        assert sleeper.calls == [3.0]


async def test_a_thousand_message_limit_paces_from_the_governor_store(client, sleeper):
    """P9: --limit 1000 is ten pages; the governor's interval store fires.

    The assert is on the governor's own reservation records, not on any
    Telethon `wait_time`: the fake client has no pacing of its own, so the
    nine sleeps can only have come from the seam's pacing store.
    """
    for _ in range(10):
        await client._call(None, history(limit=100))

    assert sleeper.calls == [3.0] * 9
    last = client._tgcli_governor.last_reserved(ACCOUNT, HISTORY_KEY)
    assert last == pytest.approx(27.0)


def test_the_interval_lookup_charges_per_unit():
    """BY_ID and MEDIA adjustments live at the charge, not in the registry."""
    assert registry.interval_for(messages.GetMessagesRequest(id=[1])) == 10.0
    file_request = upload.GetFileRequest(location=None, offset=0, limit=1)
    assert registry.interval_for(file_request) == 3.0


def test_backfill_stops_normally_when_the_breadth_budget_is_exhausted(
    config_env, monkeypatch, capsys
):
    """P8: budget hits 100 -> exit 0, stop_reason, checkpoint intact."""
    import json

    from telethon.tl.types import PeerUser

    from tests.conftest import FakeClient, make_session_fake
    from tests.test_cli_archive_phase3 import _dialog, _init, _me, _msg, _user
    from tgcli.cli import main
    from tgcli.commands import archive as archive_cmd
    from tgcli.governor.ledger import Ledger

    ledger = Ledger.open()
    me = _me(user_id=42)
    for peer in range(100):
        ledger.touch_peer(42, peer, time.time())

    alice = _user(user_id=7, username="alice")
    bob = _user(user_id=8, username="bob", first="Bob")
    client = FakeClient(
        me=me,
        entities={7: alice, 8: bob, "@alice": alice, "@bob": bob},
        messages=[_msg(mid=1, text="hi", peer=PeerUser(7))],
        dialogs=[_dialog(alice, name="Alice"), _dialog(bob, name="Bob")],
    )
    client._tgcli_governor = ledger
    client._self_id = 42
    make_session_fake(monkeypatch, client)
    _init(monkeypatch, client)
    capsys.readouterr()

    assert (
        main(
            [
                "archive",
                "backfill",
                "--private",
                "--max-dialogs",
                "5",
                "--limit",
                "10",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "breadth_budget_exhausted"
    assert data["dialogs"] == []
    assert data["deferred"] == 2
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        assert store_mod.list_sync_state(conn) == []
    finally:
        conn.close()
