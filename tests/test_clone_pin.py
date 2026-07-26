"""Pinned-message carry-over: pure decide + live sync_phase (ADR-0055)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors, utils as telethon_utils
from telethon.tl import functions, types

from tgcli import safety
from tgcli.clone import pin, state


def _pin_state(**overrides) -> pin.PinState:
    values = {"pinned_dest_id": None, "pin_occupied": False}
    values.update(overrides)
    return pin.PinState(**values)


def test_decide_unmapped_when_source_has_no_pin():
    decision = pin.decide(None, {"12": 9}, _pin_state())
    assert decision.status == "unmapped"
    assert decision.destination_id is None


def test_decide_unmapped_when_source_pin_absent_from_id_map():
    decision = pin.decide(12, {"1": 1}, _pin_state())
    assert decision.status == "unmapped"
    assert decision.destination_id is None


def test_decide_set_when_mapped_and_never_resolved():
    decision = pin.decide(12, {"12": 9}, _pin_state())
    assert decision.status == "set"
    assert decision.destination_id == 9


def test_decide_unchanged_when_clone_already_pinned_even_if_source_moved():
    decision = pin.decide(99, {"12": 9, "99": 50}, _pin_state(pinned_dest_id=9))
    assert decision.status == "unchanged"
    assert decision.destination_id == 9


def test_decide_occupied_when_destination_was_recorded_occupied():
    decision = pin.decide(12, {"12": 9}, _pin_state(pin_occupied=True))
    assert decision.status == "occupied"
    assert decision.destination_id is None


def test_clone_state_round_trips_pin_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(account_user_id=1, source_peer_id=2, source_title="S")
    saved.pinned_dest_id = 9
    state.save(saved)
    loaded = state.load(saved.clone_id)
    assert loaded.pinned_dest_id == 9
    assert loaded.pin_occupied is False

    occupied = state.CloneState.new(
        account_user_id=1, source_peer_id=3, source_title="S"
    )
    occupied.pin_occupied = True
    state.save(occupied)
    assert state.load(occupied.clone_id).pin_occupied is True
    assert state.load(occupied.clone_id).pinned_dest_id is None


def test_clone_state_defaults_pin_fields_for_legacy_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Old"
    )
    data = saved.to_dict()
    del data["pinned_dest_id"], data["pin_occupied"]
    state.clones_dir().mkdir(parents=True, exist_ok=True)
    path = state.path_for(saved.clone_id)
    path.write_text(__import__("json").dumps(data))
    loaded = state.load(saved.clone_id)
    assert loaded.pinned_dest_id is None
    assert loaded.pin_occupied is False


def test_from_dict_rejects_non_positive_pinned_dest_id():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(
            {
                "version": state.VERSION,
                "account_user_id": 1,
                "source_peer_id": 2,
                "source_title": "S",
                "pinned_dest_id": 0,
            }
        )


def test_from_dict_rejects_pin_occupied_with_pinned_dest_id():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(
            {
                "version": state.VERSION,
                "account_user_id": 1,
                "source_peer_id": 2,
                "source_title": "S",
                "pinned_dest_id": 9,
                "pin_occupied": True,
            }
        )


def test_snapshot_from_state_without_live_check():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    assert pin.snapshot(clone_state) == {
        "source_id": None,
        "destination_id": None,
        "status": "unmapped",
    }
    clone_state.pinned_dest_id = 9
    assert pin.snapshot(clone_state) == {
        "source_id": None,
        "destination_id": 9,
        "status": "set",
    }
    clone_state.pinned_dest_id = None
    clone_state.pin_occupied = True
    assert pin.snapshot(clone_state) == {
        "source_id": None,
        "destination_id": None,
        "status": "occupied",
    }


class _UserSourceClient:
    """Fakes just enough of TelegramClient to resolve a raw request's inputs.

    Mirrors ``telethon.client.users.UserMethods._call``, which always awaits
    ``request.resolve(self, utils)`` before sending. Real ``resolve()``
    implementations (generated on the ``functions`` classes) turn full
    entities into the exact Input* type Telegram expects, so replaying that
    step here is what makes the assertions below load-bearing rather than
    permissive.
    """

    def __init__(self):
        self.requests: list[object] = []

    async def get_input_entity(self, entity):
        return telethon_utils.get_input_peer(entity)

    async def __call__(self, request):
        await request.resolve(self, telethon_utils)
        self.requests.append(request)
        if isinstance(request, functions.users.GetFullUserRequest):
            return SimpleNamespace(full_user=SimpleNamespace(pinned_msg_id=44))
        raise AssertionError(f"unexpected request: {request!r}")


class _ChatSourceClient:
    """Fakes GetFullChatRequest dispatch for the basic-group source branch."""

    def __init__(self):
        self.requests: list[object] = []

    async def __call__(self, request):
        await request.resolve(self, telethon_utils)
        self.requests.append(request)
        if isinstance(request, functions.messages.GetFullChatRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(pinned_msg_id=77))
        raise AssertionError(f"unexpected request: {request!r}")


async def _identity_cooldown(make):
    """ADR-0052: the cooldown seam takes a zero-arg thunk, not an awaitable."""
    return await make()


@pytest.mark.asyncio
async def test_source_pinned_msg_id_user_branch_uses_get_full_user():
    """User sources must hit GetFullUserRequest with a resolved InputUser."""
    source = types.User(id=123, access_hash=555, first_name="Alice")
    client = _UserSourceClient()

    result = await pin._source_pinned_msg_id(client, source, _identity_cooldown)

    assert result == 44
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.users.GetFullUserRequest)
    assert isinstance(request.id, types.InputUser)
    assert request.id.user_id == 123
    assert request.id.access_hash == 555


@pytest.mark.asyncio
async def test_source_pinned_msg_id_chat_branch_uses_get_full_chat():
    """Basic-group sources must hit GetFullChatRequest keyed by chat_id."""
    source = types.Chat(
        id=456,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )
    client = _ChatSourceClient()

    result = await pin._source_pinned_msg_id(client, source, _identity_cooldown)

    assert result == 77
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.GetFullChatRequest)
    assert request.chat_id == 456


class _PinClient:
    """Records GetFullChannel + UpdatePinnedMessage for boundary tests."""

    def __init__(
        self,
        *,
        source_pinned: int | None,
        dest_pinned: int | None = None,
        flood_on_pin: bool = False,
    ):
        self.source = SimpleNamespace(id=123, title="Source")
        self.destination = SimpleNamespace(id=999, title="Dest", creator=True)
        self.destination_input = types.InputPeerChannel(channel_id=999, access_hash=555)
        self.source_pinned = source_pinned
        self.dest_pinned = dest_pinned
        self.flood_on_pin = flood_on_pin
        self.requests: list[object] = []

    async def get_input_entity(self, entity):
        if entity is self.destination:
            return self.destination_input
        raise AssertionError(f"unexpected get_input_entity: {entity!r}")

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            channel = request.channel
            pinned = (
                self.source_pinned
                if getattr(channel, "id", None) == self.source.id
                else self.dest_pinned
            )
            return SimpleNamespace(full_chat=SimpleNamespace(pinned_msg_id=pinned))
        if isinstance(request, functions.messages.UpdatePinnedMessageRequest):
            if self.flood_on_pin:
                raise telethon_errors.FloodWaitError(request=request, capture=30)
            return True
        raise AssertionError(f"unexpected request: {request!r}")


def _ready_clone(*, mapped: bool = True) -> state.CloneState:
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source"
    )
    clone_state.destination_peer_id = 999
    if mapped:
        clone_state.record_mapping(12, 9)
    return clone_state


@pytest.mark.asyncio
async def test_sync_phase_pins_mapped_source_when_destination_empty(monkeypatch):
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=12, dest_pinned=None)
    clone_state = _ready_clone()

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": 12, "destination_id": 9, "status": "set"}
    assert clone_state.pinned_dest_id == 9
    assert clone_state.pin_occupied is False
    fulls = [
        r
        for r in client.requests
        if isinstance(r, functions.channels.GetFullChannelRequest)
    ]
    assert len(fulls) == 2
    assert getattr(fulls[0].channel, "id", None) == 123
    assert getattr(fulls[1].channel, "id", None) == 999
    pins = [
        r
        for r in client.requests
        if isinstance(r, functions.messages.UpdatePinnedMessageRequest)
    ]
    assert len(pins) == 1
    assert pins[0].peer is client.destination_input
    assert isinstance(pins[0].peer, types.InputPeerChannel)
    assert pins[0].id == 9
    assert pins[0].silent is True
    assert not pins[0].unpin
    assert audits == [
        (
            "clone-sync-pin",
            "main",
            {
                "clone_id": clone_state.clone_id,
                "source_message_id": 12,
                "destination_message_id": 9,
            },
        )
    ]


@pytest.mark.asyncio
async def test_sync_phase_occupied_when_destination_already_pins(monkeypatch):
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=12, dest_pinned=77)
    clone_state = _ready_clone()

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": 12, "destination_id": None, "status": "occupied"}
    assert clone_state.pin_occupied is True
    assert clone_state.pinned_dest_id is None
    assert not any(
        isinstance(r, functions.messages.UpdatePinnedMessageRequest)
        for r in client.requests
    )
    assert audits == []


@pytest.mark.asyncio
async def test_sync_phase_recovers_own_pin_after_crash_before_save(monkeypatch):
    """A crash between the pin RPC and the state save must not latch occupied.

    The destination carries exactly the pin this run would set — the pin a
    crashed previous run placed. The phase adopts it: `pinned_dest_id` is
    repaired and saved, no pin mutation is issued, and no `clone-sync-pin`
    audit row is written (CONTRACT §11 reserves "set" for a mutation this run
    performed, so the recovery reports "unchanged").
    """
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=12, dest_pinned=9)
    clone_state = _ready_clone()

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": 12, "destination_id": 9, "status": "unchanged"}
    assert clone_state.pinned_dest_id == 9
    assert clone_state.pin_occupied is False
    reloaded = state.load(clone_state.clone_id)
    assert reloaded is not None
    assert reloaded.pinned_dest_id == 9
    assert reloaded.pin_occupied is False
    fulls = [
        r
        for r in client.requests
        if isinstance(r, functions.channels.GetFullChannelRequest)
    ]
    assert len(fulls) == 2  # ADR-0055 budget: recovery adds no extra RPCs
    assert not any(
        isinstance(r, functions.messages.UpdatePinnedMessageRequest)
        for r in client.requests
    )
    assert audits == []


@pytest.mark.asyncio
async def test_sync_phase_occupied_when_destination_pins_other_cloned_post(
    monkeypatch,
):
    """ADR-0055 decision 2: a human-chosen pin outranks the mirrored one.

    Recovery is strictly the exact destination id this run intended to set; a
    different pinned message still latches occupied even when it is itself a
    cloned post (present in ``id_map.values()``).
    """
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=12, dest_pinned=50)
    clone_state = _ready_clone()
    clone_state.record_mapping(99, 50)

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": 12, "destination_id": None, "status": "occupied"}
    assert clone_state.pin_occupied is True
    assert clone_state.pinned_dest_id is None
    assert not any(
        isinstance(r, functions.messages.UpdatePinnedMessageRequest)
        for r in client.requests
    )
    assert audits == []


@pytest.mark.asyncio
async def test_sync_phase_second_run_uses_state_only_zero_rpcs(monkeypatch):
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=99, dest_pinned=None)
    clone_state = _ready_clone()
    clone_state.record_mapping(99, 50)
    clone_state.pinned_dest_id = 9

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": None, "destination_id": 9, "status": "unchanged"}
    assert client.requests == []
    assert audits == []


@pytest.mark.asyncio
async def test_sync_phase_unmapped_when_source_has_no_pin(monkeypatch):
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )
    client = _PinClient(source_pinned=None)
    clone_state = _ready_clone()

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    result = await pin.sync_phase(
        client,
        clone_state,
        client.source,
        client.destination,
        mutate,
        cooldown,
        "main",
    )

    assert result == {"source_id": None, "destination_id": None, "status": "unmapped"}
    assert clone_state.pinned_dest_id is None
    assert clone_state.pin_occupied is False
    assert len(client.requests) == 1
    assert isinstance(client.requests[0], functions.channels.GetFullChannelRequest)
    assert getattr(client.requests[0].channel, "id", None) == 123
    assert audits == []


@pytest.mark.asyncio
async def test_sync_phase_floodwait_on_pin_leaves_state_unset(monkeypatch):
    monkeypatch.setattr(safety, "append_audit", lambda *a, **k: None)
    client = _PinClient(source_pinned=12, dest_pinned=None, flood_on_pin=True)
    clone_state = _ready_clone()

    async def mutate(request):
        # Mirror commands.clone._mutate / _with_cooldown: persist then re-raise.
        try:
            return await client(request)
        except telethon_errors.FloodWaitError as exc:
            deadline = datetime.now(UTC) + timedelta(seconds=exc.seconds)
            clone_state.set_cooldown(deadline)
            state.save(clone_state)
            raise

    async def cooldown(make):
        return await make()

    with pytest.raises(telethon_errors.FloodWaitError):
        await pin.sync_phase(
            client,
            clone_state,
            client.source,
            client.destination,
            mutate,
            cooldown,
            "main",
        )

    assert clone_state.pinned_dest_id is None
    assert clone_state.cooldown_deadline() is not None


@pytest.mark.asyncio
async def test_sync_phase_no_audit_on_unmapped_occupied_unchanged(monkeypatch):
    audits: list[tuple] = []
    monkeypatch.setattr(
        safety,
        "append_audit",
        lambda action, account, details: audits.append((action, account, details)),
    )

    async def mutate(request):
        return await client(request)

    async def cooldown(make):
        return await make()

    for kwargs, setup in (
        ({"source_pinned": None}, lambda s: None),
        ({"source_pinned": 12, "dest_pinned": 1}, lambda s: None),
        ({"source_pinned": 12}, lambda s: setattr(s, "pinned_dest_id", 9)),
    ):
        client = _PinClient(**kwargs)
        clone_state = _ready_clone()
        setup(clone_state)
        await pin.sync_phase(
            client,
            clone_state,
            client.source,
            client.destination,
            mutate,
            cooldown,
            "main",
        )
    assert audits == []
