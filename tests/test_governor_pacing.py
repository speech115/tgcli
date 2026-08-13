"""Pacing and the breadth budget (ADR-0072 decision 3, plan phase 4).

Covers #139's matrix rows P1-P9, C1 and C5. No test sleeps in real time:
a fake clock and a recording sleep function stand in for both.
"""

import time

import pytest
from telethon.tl.functions import messages, updates, upload
from telethon.tl.types import InputPeerUser

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


@pytest.fixture(autouse=True)
def clean_pacing_state():
    """m5 review fix: the process-wide pacing counters are shared state;
    every test starts from a clean slate so order cannot leak."""
    from tgcli.governor import pacing

    pacing.reset_runtime()
    yield
    pacing.reset_runtime()


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


async def test_upload_parts_are_not_paced_per_part(client, sleeper):
    """Review C2: SaveFilePart has no offset — a multi-part upload must not
    pay 3 s per part (it is one file, like the download side)."""
    parts = [
        upload.SaveFilePartRequest(file_id=1, file_part=0, bytes=b"a" * 100),
        upload.SaveFilePartRequest(file_id=1, file_part=1, bytes=b"b" * 100),
        upload.SaveFilePartRequest(file_id=1, file_part=2, bytes=b"c" * 100),
    ]
    big = upload.SaveBigFilePartRequest(
        file_id=2, file_part=0, file_total_parts=3, bytes=b"d" * 100
    )

    for part in parts + [big]:
        await client._call(None, part)

    assert sleeper.calls == []


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


def test_touch_history_peer_returns_false_only_when_budget_refuses(ledger):
    """ADR-0117: False means budget-full new peer; non-HISTORY stays True."""
    now = 1_000_000.0
    peer_a = InputPeerUser(user_id=101, access_hash=1)
    peer_b = InputPeerUser(user_id=202, access_hash=1)
    req_a = history(peer=peer_a)
    req_b = history(peer=peer_b)

    assert pacing.touch_history_peer(ledger, ACCOUNT, req_a, now=now) is True
    # Fill the remaining default budget with distinct peers.
    while ledger.breadth_remaining(ACCOUNT, now) > 0:
        next_id = ledger.peers_in_window(ACCOUNT, now) + 10_000
        assert ledger.touch_peer(ACCOUNT, next_id, now) is True
    assert pacing.budget_ok(ledger, ACCOUNT, now=now) is False
    assert pacing.touch_history_peer(ledger, ACCOUNT, req_b, now=now) is False
    # Non-HISTORY is not a breadth spend — True, not "budget denied".
    assert (
        pacing.touch_history_peer(ledger, ACCOUNT, updates.GetStateRequest(), now=now)
        is True
    )
    # Already-counted peer may refresh without spending a new slot.
    assert pacing.touch_history_peer(ledger, ACCOUNT, req_a, now=now + 1) is True


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


def test_the_deadline_does_not_count_governed_sleep(monkeypatch):
    """D1: --timeout counts only ungoverned time (ADR-0072 decision 6).

    The run's *active* governed sleep is discounted write-ahead: a task
    sleeping a flood out past the wall-clock deadline must survive, because
    the governor decided to sleep it.
    """
    import asyncio

    from tgcli import cli
    from tgcli.governor import pacing

    pacing.reset_runtime(cap=120.0)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        # A long governed flood-sleep: wall time advances while the sleep is
        # still in flight. The deadline must see it as governed immediately.
        await asyncio.sleep(0.2)

    async def flood_sleep():
        return await pacing.sleep_flood(60.0, sleep=fake_sleep)

    # 0.1 s deadline, 0.2 s of governed flood-sleep: the sleep is reserved
    # before it happens, so the deadline grants it back and the run wins.
    assert cli._run_with_deadline(flood_sleep(), 0.1) is True
    assert sleeps == [60.0]


def test_a_flood_that_fits_the_wall_clock_cap_is_slept_out(monkeypatch):
    """D5a: retry_after=8 under a 10 s cap sleeps and succeeds."""
    import asyncio

    from tgcli.governor import pacing

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    pacing.reset_runtime(cap=10.0)

    assert asyncio.run(pacing.sleep_flood(8.0, sleep=fake_sleep)) is True
    assert sleeps == [8.0]


def test_a_flood_beyond_the_cap_exits_without_sleeping(monkeypatch):
    """D5b: retry_after=15 under a 10 s cap exits 5 with zero sleep calls."""
    import asyncio

    from tgcli.governor import pacing

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    pacing.reset_runtime(cap=10.0)

    assert asyncio.run(pacing.sleep_flood(15.0, sleep=fake_sleep)) is False
    assert sleeps == []


def test_a_long_paced_run_is_not_killed_by_the_default_deadline(monkeypatch):
    """D6: 75 s of governed sleep under a 60 s default deadline completes.

    The deadline counts only ungoverned time — the sum total of governed
    sleep is discounted — so a run that spends its wall time in deliberate
    intervals survives a deadline shorter than the sum of its sleeps.
    Without the discount the run would be cancelled at the deadline.
    """
    import asyncio

    from tgcli import cli
    from tgcli.governor import pacing

    pacing.reset_runtime(cap=300.0)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        # Real wall time advances while each paced interval is in flight.
        await asyncio.sleep(0.02)

    async def paced_work():
        for _ in range(25):  # 25 x 3 s pacing intervals = 75 s governed sleep
            await pacing.sleep_flood(3.0, sleep=fake_sleep)
        return "done"

    # 0.3 s deadline, 0.5 s of real wall time spent in governed sleeps: the
    # write-ahead discount lets the run finish instead of being killed.
    assert cli._run_with_deadline(paced_work(), 0.3) == "done"
    assert len(sleeps) == 25


def test_backfill_stops_normally_when_the_wall_clock_cap_is_exhausted(
    config_env, monkeypatch, capsys
):
    """D3/D4: --max-runtime exhausted mid-run -> exit 0, stop_reason, checkpoint."""
    import json

    from telethon.tl.types import PeerUser

    from tests.conftest import FakeClient, make_session_fake
    from tests.test_cli_archive_phase3 import _dialog, _init, _me, _msg, _user
    from tgcli.cli import main
    from tgcli.commands import archive as archive_cmd
    from tgcli.governor import pacing

    # Deterministic (L9 pattern): the cap is always exhausted at the first
    # check, whatever the real wall clock says. `main` re-arms the runtime
    # from --max-runtime, so a real-time race must not decide this test.
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)

    me = _me(user_id=42)
    alice = _user(user_id=7, username="alice")
    client = FakeClient(
        me=me,
        entities={7: alice, "@alice": alice},
        messages=[_msg(mid=1, text="hi", peer=PeerUser(7))],
        dialogs=[_dialog(alice, name="Alice")],
    )
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
                "--max-runtime",
                "0.001",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert data["dialogs"] == []
    assert data["deferred"] == 1
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        assert store_mod.list_sync_state(conn) == []
    finally:
        conn.close()


def test_deadline_defaults_match_the_contract(monkeypatch):
    """D2: the deadline is a hang detector — governed sleep is exempt, and
    long-running commands keep no implicit deadline (CONTRACT §1)."""
    from tgcli import cli
    from tgcli.parser import build_parser

    parser = build_parser()

    def default_timeout_for(argv):
        args = parser.parse_args(argv)
        cli._apply_global_defaults(args)
        return args.timeout

    # Short commands: uniform 60 s default.
    assert default_timeout_for(["dialogs"]) == 60.0
    assert default_timeout_for(["changes"]) == 60.0
    assert default_timeout_for(["send", "@x", "hi", "--preview"]) == 60.0
    # Long-running commands keep no implicit deadline (CONTRACT §1): an
    # implicit 60 s would kill them mid-run.
    assert default_timeout_for(["clone", "init", "@s"]) is None
    assert default_timeout_for(["clone", "sync", "@s"]) is None
    assert default_timeout_for(["clone", "refresh", "@s"]) is None
    assert default_timeout_for(["jobs", "run", "--lane", "local"]) is None
    assert default_timeout_for(["export", "messages", "@c", "--output", "x"]) is None
    assert default_timeout_for(["media", "download", "@c", "1"]) is None
    # CONTRACT §10/§12: phone start uses the ordinary detector; long-poll and
    # interactive continuation own their operator-time boundary.
    assert default_timeout_for(["changes", "--wait", "300"]) is None
    assert default_timeout_for(["accounts", "login", "main", "--phone", "+1"]) == 60.0
    assert default_timeout_for(["accounts", "login", "main", "--continue", "c"]) is None
    # The exemption list is data, not deadline logic: the detector itself
    # has no command branches.
    assert not hasattr(cli, "_deadline")


def _journal_lines():
    import json

    from tgcli.session import state_dir

    path = state_dir() / "invocations.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_journal_carries_governor_fields_on_a_flood_stop(
    config_env, monkeypatch, capsys
):
    """L6-L8: retry_after, request_type and provenance land in the journal.

    The seam records the stop via pacing.note_stop when a flood arms (its
    unit tests pin that); this pins that cli.main forwards the governor's
    per-invocation accounting into the journal row.
    """
    from tgcli import dispatch
    from tgcli.cli import main
    from tgcli.errors import RateLimitError
    from tgcli.governor import pacing

    async def flood_run_network(args, account):
        pacing.note_stop(
            retry_after=123,
            request_type="updates.GetStateRequest",
            provenance="server",
        )
        raise RateLimitError("rate limited for 123s", retry_after=123)

    monkeypatch.setattr(dispatch, "run_network", flood_run_network)

    assert main(["dialogs", "--json"]) == 5
    capsys.readouterr()

    [entry] = _journal_lines()
    assert entry["retry_after"] == 123
    assert entry["request_type"] == "updates.GetStateRequest"
    assert entry["provenance"] == "server"
    # The refusal happened before any request reached the seam, so no
    # governor accounting fields (review fix m1).
    assert "request_count" not in entry
    assert "governed_sleep_ms" not in entry


def test_journal_carries_stop_reason_on_a_normal_stop(config_env, monkeypatch, capsys):
    """L9: breadth and wall-clock stops are distinguishable in the journal."""

    from telethon.tl.types import PeerUser

    from tests.conftest import FakeClient, make_session_fake
    from tests.test_cli_archive_phase3 import _dialog, _init, _me, _msg, _user
    from tgcli.cli import main
    from tgcli.governor import pacing

    # Deterministic: the cap is always exhausted at the first check, instead
    # of racing a real 0.001 s wall-clock window.
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)
    me = _me(user_id=42)
    alice = _user(user_id=7, username="alice")
    client = FakeClient(
        me=me,
        entities={7: alice, "@alice": alice},
        messages=[_msg(mid=1, text="hi", peer=PeerUser(7))],
        dialogs=[_dialog(alice, name="Alice")],
    )
    make_session_fake(monkeypatch, client)
    _init(monkeypatch, client)
    capsys.readouterr()

    args = ["archive", "backfill", "--private", "--max-dialogs", "5", "--json"]
    assert main(args) == 0
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["stop_reason"] == "wall_clock_cap"
    assert entry["exit_code"] == 0


def test_journal_counts_governed_sleep_and_requests(config_env, monkeypatch, capsys):
    """L11: governed_sleep_ms and request_count match what the run did."""
    from tgcli import dispatch
    from tgcli.cli import main
    from tgcli.governor import pacing

    async def paced_run_network(args, account):
        # The seam counts each governed request and its deliberate sleep.
        # sleep_flood owes the sleep only when a wall-clock cap is set.
        import asyncio

        pacing.note_request()
        await pacing.sleep_flood(3.0, sleep=lambda s: asyncio.sleep(0))
        pacing.note_request()
        return {"dialogs": []}, []

    monkeypatch.setattr(dispatch, "run_network", paced_run_network)

    assert main(["--max-runtime", "30", "dialogs", "--json"]) == 0
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["governed_sleep_ms"] == 3000
    assert entry["request_count"] == 2


def test_journal_omits_flood_fields_on_a_survived_flood(
    config_env, monkeypatch, capsys
):
    """M1 review fix: a flood that was slept out and survived is not a
    flood-related exit — the journal carries no retry_after/provenance on
    the successful run that absorbed it."""
    from tgcli import dispatch
    from tgcli.cli import main
    from tgcli.governor import pacing

    async def survived_flood(args, account):
        # The seam recorded the flood at arming...
        pacing.note_stop(
            retry_after=5,
            request_type="messages.GetHistoryRequest",
            provenance="server",
        )
        # ...but the run slept it out and completed.
        return {"dialogs": []}, []

    monkeypatch.setattr(dispatch, "run_network", survived_flood)

    assert main(["dialogs", "--json"]) == 0
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["exit_code"] == 0
    assert "retry_after" not in entry
    assert "request_type" not in entry
    assert "provenance" not in entry


def test_journal_omits_flood_fields_when_final_error_is_not_flood(
    config_env, monkeypatch, capsys
):
    """T16: a survived flood must not pollute a later NOT_FOUND journal row."""
    from tgcli import dispatch
    from tgcli.cli import main
    from tgcli.errors import NotFoundError
    from tgcli.governor import pacing

    async def flood_then_missing(args, account):
        pacing.note_stop(
            retry_after=5,
            request_type="messages.GetHistoryRequest",
            provenance="server",
        )
        raise NotFoundError("dialog not found")

    monkeypatch.setattr(dispatch, "run_network", flood_then_missing)

    assert main(["dialogs", "--json"]) == 4
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["exit_code"] == 4
    assert entry.get("error") == "NOT_FOUND"
    assert "retry_after" not in entry
    assert "request_type" not in entry
    assert "provenance" not in entry


def test_journal_keeps_flood_fields_on_a_refused_flood(config_env, monkeypatch, capsys):
    """L6-L8: a run that actually ended on a refusal still carries them."""
    from tgcli import dispatch
    from tgcli.cli import main
    from tgcli.errors import RateLimitError
    from tgcli.governor import pacing

    async def refused(args, account):
        pacing.note_stop(
            retry_after=123,
            request_type="messages.GetHistoryRequest",
            provenance="account_cooldown",
        )
        raise RateLimitError("rate limited for 123s", retry_after=123)

    monkeypatch.setattr(dispatch, "run_network", refused)

    assert main(["dialogs", "--json"]) == 5
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["retry_after"] == 123
    assert entry["request_type"] == "messages.GetHistoryRequest"
    assert entry["provenance"] == "account_cooldown"


def test_max_runtime_must_be_positive(config_env, capsys):
    """Review fix: --max-runtime 0 / negative is misuse, exit 2, not a no-op."""
    from tgcli.cli import main

    assert main(["--max-runtime", "0", "dialogs", "--json"]) == 2
    assert "positive" in capsys.readouterr().err.lower()
    assert main(["--max-runtime", "-5", "dialogs", "--json"]) == 2


@pytest.mark.parametrize("flag", ["--max-runtime", "--timeout"])
def test_runtime_bounds_must_be_finite(config_env, capsys, flag):
    from tgcli.cli import main

    assert main([flag, "nan", "dialogs", "--json"]) == 2
    assert "finite" in capsys.readouterr().err.lower()


async def test_resolve_twice_within_three_seconds_paces_like_today(client, sleeper):
    """P6: ResolvePhoneRequest keeps its 3 s pace via the general mechanism.

    The file-based pre-flight gate in resolve_phone.py stays (its own tests
    pin it); this pins that the seam's pacing covers the same request type
    at the same interval.
    """
    from telethon.tl.functions.contacts import ResolvePhoneRequest

    await client._call(None, ResolvePhoneRequest(phone="+100"))
    await client._call(None, ResolvePhoneRequest(phone="+200"))

    assert sleeper.calls == [3.0]


def test_journal_records_resolve_phone_cooldown_provenance(
    config_env, monkeypatch, capsys
):
    """m7 review fix: the third provenance value lands in the journal.

    The pre-flight resolve_phone gate records its own stop; a second
    resolve within 3 s exits 5 with provenance=resolve_phone_cooldown.
    """

    from tgcli import resolve_phone
    from tgcli.cli import main
    from tgcli.session import state_dir

    (state_dir() / "sessions").mkdir(parents=True, exist_ok=True)
    (state_dir() / "sessions" / "main.session").write_bytes(b"x")

    clock = {"t": 1_000.0}
    monkeypatch.setattr(resolve_phone.time, "time", lambda: clock["t"])

    from telethon.tl import types

    from tests.conftest import FakeClient, make_session_fake
    from tests.test_cli_archive_phase3 import _me

    user = _me(user_id=42)
    resolved = types.PeerUser(user_id=42)
    client = FakeClient(
        me=user,
        resolve_phone_result=type(
            "R", (), {"peer": resolved, "users": [user], "chats": []}
        )(),
    )
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "+99512345678", "--json"]) == 0
    capsys.readouterr()
    assert main(["resolve", "+99512345678", "--json"]) == 5
    capsys.readouterr()

    entry = _journal_lines()[-1]
    assert entry["provenance"] == "resolve_phone_cooldown"
    assert entry["request_type"] == "contacts.ResolvePhoneRequest"
    assert entry["retry_after"] >= 1


def test_timeout_must_be_positive(config_env, capsys):
    """Review D2: --timeout 0 / negative is misuse, exit 2, not a split
    personality between local and network commands."""
    from tgcli.cli import main

    assert main(["--timeout", "0", "dialogs", "--json"]) == 2
    assert "positive" in capsys.readouterr().err.lower()
    assert main(["--timeout", "-1", "dialogs", "--json"]) == 2


async def test_the_reservation_loser_wait_to_the_next_slot(clock, sleeper, tmp_path):
    """Review blocker 3: a process that loses the reservation race must not
    dispatch with zero spacing from the winner — it waits to the winner's
    slot plus the interval and claims that."""
    from tgcli.governor import pacing
    from tgcli.governor.ledger import Ledger

    path = tmp_path / "governor.db"
    with Ledger.open(path) as ledger:
        # A competitor already reserved t=2.0; we start at the same instant.
        ledger.reserve(ACCOUNT, HISTORY_KEY, 2.0)
        clock["t"] = 2.0

        calls = []

        async def racing_sleep(seconds):
            calls.append(seconds)
            if seconds == 3.0 and len(calls) == 1:
                # While we sleep the interval out, the competitor reaches its
                # dispatch moment at exactly our moment (t=5.0) — the race.
                ledger.reserve(ACCOUNT, HISTORY_KEY, clock["t"] + seconds)
            clock["t"] += seconds

        await pacing.pace_before_dispatch(
            ledger, ACCOUNT, history(), now=clock["t"], sleep=racing_sleep
        )

        # We waited to the competitor's slot + the 3 s interval (t=8.0),
        # not just to the competitor's slot (t=5.0).
        assert calls == [3.0, 3.0]
        assert ledger.last_reserved(ACCOUNT, HISTORY_KEY) == 8.0


async def test_a_lost_reservation_with_an_unreadable_ledger_dispatches(
    clock, sleeper, tmp_path
):
    """Review major 1: when the reservation loses AND the ledger cannot be
    read back (degraded mode), the pace must fail open — dispatch now —
    rather than spin in a retry loop with no sleep. A long-running command
    without a default deadline would otherwise hang forever."""
    from tgcli.governor.ledger import Ledger

    path = tmp_path / "governor.db"
    ledger = Ledger.open(path)
    # Break the connection from underneath: every read and write now raises,
    # which is exactly the degraded state `reserve` returning False with
    # `last_reserved` returning None models.
    ledger._db.close()

    clock["t"] = 10.0
    await pacing.pace_before_dispatch(
        ledger, ACCOUNT, history(), now=clock["t"], sleep=sleeper
    )

    # Failed open: no sleep was owed, no infinite retry, the caller may
    # dispatch immediately. The clock is untouched and the sleep log empty.
    assert clock["t"] == 10.0
    assert sleeper.calls == []
