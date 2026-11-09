"""Random runs of FloodWaits, dropped connections and odd replies through the gate.

`test_governor_gate.py` pins each rule of the governed ``_call`` seam
(ADR-0072) one scripted case at a time. This file throws seeded random
*sequences* at it: several request types, each call answered by a scripted
mix of success, FloodWait of any length, premium and slow-mode waits, server
errors and network drops, under a random ``--max-runtime`` cap. After every
call it checks the invariants the incident runbook leans on:

* a refusal never sends an RPC, and only happens while that type is cooling;
* a request is sent at most twice, and the second send only follows a flood
  the cap could absorb;
* every server flood arms its own type, never another, and a recorded end
  never moves earlier;
* anything that is not a flood passes through untouched and arms nothing;
* the governed-sleep total equals what was actually slept.

Nothing here opens a network connection: the client is a fake ``_call``.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypothesis import example, given, settings, strategies as st
from telethon import errors as telethon_errors
from telethon.tl.functions import contacts, messages, updates

from tgcli.errors import RateLimitError
from tgcli.governor import gate, pacing, registry
from tgcli.governor.ledger import MAX_COOLDOWN_S, Ledger

DETERMINISTIC = settings(max_examples=400, derandomize=True, deadline=None)
ACCOUNT = 7091037467


def _history():
    return messages.GetHistoryRequest(
        peer="peer",
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=100,
        max_id=0,
        min_id=0,
        hash=0,
    )


REQUESTS = (
    _history,
    lambda: messages.SendMessageRequest("peer", "hi"),
    lambda: contacts.ResolveUsernameRequest("someone"),
    lambda: updates.GetStateRequest(),
)

FLOOD_SECONDS = st.sampled_from([0, 1, 5, 30, 119, 600, 3600, MAX_COOLDOWN_S, 10**7])


def _flood(kind: str, seconds: int) -> Exception:
    factory = {
        "flood": telethon_errors.FloodWaitError,
        "premium": telethon_errors.FloodPremiumWaitError,
        "slowmode": telethon_errors.SlowModeWaitError,
    }[kind]
    return factory(request=None, capture=seconds)


def _failure(kind: str) -> Exception:
    return {
        "connection": lambda: ConnectionError(
            "Connection to Telegram failed 5 time(s)"
        ),
        "reset": lambda: ConnectionResetError(104, "Connection reset by peer"),
        "socket_timeout": lambda: TimeoutError("timed out"),
        "server": lambda: telethon_errors.ServerError(
            request=None, message="INTERNAL", code=500
        ),
        "rpc_timeout": lambda: telethon_errors.TimedOutError(
            request=None, message="TIMEOUT", code=-503
        ),
    }[kind]()


OUTCOME = st.one_of(
    st.just(("ok", None)),
    st.just(("none", None)),
    st.tuples(st.sampled_from(["flood", "premium", "slowmode"]), FLOOD_SECONDS),
    st.tuples(
        st.sampled_from(
            ["connection", "reset", "socket_timeout", "server", "rpc_timeout"]
        ),
        st.none(),
    ),
)
CALL = st.tuples(
    st.integers(min_value=0, max_value=len(REQUESTS) - 1),
    st.lists(OUTCOME, min_size=2, max_size=2),
)
SCENARIO = st.tuples(
    st.none() | st.integers(min_value=0, max_value=4000),
    st.lists(CALL, min_size=1, max_size=12),
)


class ScriptedClient:
    """A fake ``_call`` that answers each send from a per-call script."""

    def __init__(self) -> None:
        self._self_id = ACCOUNT
        self.sent: list[object] = []
        self.script: list[tuple[str, int | None]] = []
        self.raised: list[BaseException] = []

    async def _call(self, sender, request, *args, **kwargs):
        self.sent.append(request)
        kind, seconds = self.script.pop(0)
        if kind == "ok":
            return "result"
        if kind == "none":
            return None  # a reply with nothing in it is still a reply
        error = _flood(kind, seconds) if seconds is not None else _failure(kind)
        self.raised.append(error)
        raise error


def _ends(ledger: Ledger, moment: datetime) -> dict[str, datetime | None]:
    # One fixed moment for every read, so a one-second cooldown cannot expire
    # between the before and after snapshots of a call.
    keys = {registry.request_key(make()) for make in REQUESTS}
    return {key: ledger.cooldown_deadline(ACCOUNT, key, now=moment) for key in keys}


async def _run(cap: int | None, calls, ledger: Ledger) -> None:
    pacing.reset_runtime(cap=cap)
    moment = datetime.now(UTC)
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    client = ScriptedClient()
    gate.install(client, ledger, sleep=sleep, clock=lambda: now[0])
    floods_armed = 0

    for index, script in calls:
        request = REQUESTS[index]()
        key = registry.request_key(request)
        before = _ends(ledger, moment)
        waiting = gate.cooling_seconds(ledger, ACCOUNT, key)
        client.script = list(script)
        sent_before = len(client.sent)
        raised_before = len(client.raised)
        slept_before = len(slept)
        started = datetime.now(UTC)
        outcome: BaseException | object
        try:
            outcome = await client._call(None, request)
        except BaseException as exc:  # noqa: BLE001 - the invariant is the type
            outcome = exc

        sends = len(client.sent) - sent_before
        new_errors = client.raised[raised_before:]
        after = _ends(ledger, moment)
        arming = [
            error
            for error in new_errors
            if isinstance(error, gate.ARMING_ERRORS) and error.seconds > 0
        ]
        floods_armed += len(arming)

        assert sends <= 2
        if isinstance(outcome, RateLimitError):
            assert sends == 0, "a refusal must not reach Telegram"
            assert waiting > 0, "only a cooling type may be refused"
            # A wait the cap can absorb is slept out, not refused. Fake sleeps
            # do not use the real clock, so the margin only covers test time.
            assert cap is None or waiting > cap - 30, "refused a wait that fit the cap"
            assert outcome.details["retry_after"] > 0
        elif isinstance(outcome, BaseException):
            assert new_errors and outcome is new_errors[-1], (
                "the gate must re-raise the fake's own error, untouched"
            )
        else:
            assert outcome in ("result", None)

        if waiting > 0 and sends > 0:
            # Sent while cooling: only after sleeping the recorded wait out.
            assert any(abs(pause - waiting) <= 1 for pause in slept[slept_before:]), (
                "sent into a live cooldown without waiting"
            )
        if sends == 2:
            first = new_errors[0]
            assert isinstance(first, gate.ARMING_ERRORS)
            assert cap is not None and first.seconds <= cap
            assert first.seconds in slept[slept_before:]

        for other, end in after.items():
            if other != key or not arming:
                assert end == before[other], f"{other} changed without its own flood"
        if arming:
            end = after[key]
            assert end is not None
            longest = max(min(error.seconds, MAX_COOLDOWN_S) for error in arming)
            assert end >= started + timedelta(seconds=longest - 5)
            if before[key] is not None:
                assert end >= before[key], "a recorded end moved earlier"

    assert abs(pacing.total_governed_sleep() - sum(slept)) < 1e-6
    count, _ = pacing.flood_tally()
    assert count == floods_armed


@DETERMINISTIC
@given(SCENARIO)
# A zero-second flood is retried under a cap but must arm nothing.
@example((None, [(0, [("flood", 0), ("ok", None)])]))
@example((60, [(0, [("premium", 0), ("ok", None)]), (0, [("ok", None), ("ok", None)])]))
# A recorded wait inside the cap is slept out on the next call.
@example(
    (
        4000,
        [
            (1, [("flood", 600), ("connection", None)]),
            (1, [("ok", None), ("ok", None)]),
        ],
    )
)
def test_random_flood_and_network_runs_keep_the_gate_invariants(scenario):
    cap, calls = scenario
    with tempfile.TemporaryDirectory() as directory:
        with Ledger.open(Path(directory) / "governor.db") as ledger:
            asyncio.run(_run(cap, calls, ledger))


def test_a_connection_drop_on_the_flood_retry_is_raised_as_is():
    """Flood, sleep it out, then the network goes: the drop is what surfaces."""
    with tempfile.TemporaryDirectory() as directory:
        with Ledger.open(Path(directory) / "governor.db") as ledger:
            asyncio.run(_run(60, [(0, [("flood", 5), ("connection", None)])], ledger))
            assert (
                gate.cooling_seconds(ledger, ACCOUNT, "messages.GetHistoryRequest") > 0
            )
