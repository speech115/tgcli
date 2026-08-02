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


async def test_the_cdn_client_telethon_builds_itself_is_governed(ledger):
    """S6: `_get_cdn_client` constructs a fresh client via `self.__class__`.

    That child never passes through `session._make_client`, so instance
    patching the parent is not enough — it would fetch CDN file bytes
    ungoverned, with Telethon's own sleeper still absorbing floods.
    """
    child = FakeClient(self_id=None)
    child.flood_sleep_threshold = 60
    parent = FakeClient()

    async def make_cdn(cdn_redirect):
        return child

    parent._get_cdn_client = make_cdn
    gate.install(parent, ledger)
    arm(ledger, key="upload.GetCdnFileRequest")

    produced = await parent._get_cdn_client(object())

    assert produced.flood_sleep_threshold == 0
    with pytest.raises(RateLimitError):
        await produced._call(
            "cdn-sender", upload.GetCdnFileRequest(file_token=b"t", offset=0, limit=1)
        )
    assert produced.sent == []


async def test_the_cdn_child_inherits_the_parent_account(ledger):
    """A fresh client has no `_self_id`; without inheritance it slips the gate."""
    child = FakeClient(self_id=None)
    child.flood_sleep_threshold = 60
    parent = FakeClient()

    async def make_cdn(cdn_redirect):
        return child

    parent._get_cdn_client = make_cdn
    gate.install(parent, ledger)

    produced = await parent._get_cdn_client(object())
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 300
    produced._raises = error

    with pytest.raises(telethon_errors.FloodWaitError):
        await produced._call(
            "cdn-sender", upload.GetCdnFileRequest(file_token=b"t", offset=0, limit=1)
        )

    assert ledger.cooldown_deadline(ACCOUNT, "upload.GetCdnFileRequest") is not None


async def test_a_premium_media_flood_also_arms(ledger):
    error = telethon_errors.FloodPremiumWaitError(request=None)
    error.seconds = 900
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodPremiumWaitError):
        await client._call(
            None, upload.GetFileRequest(location=None, offset=0, limit=1)
        )

    assert ledger.cooldown_deadline(ACCOUNT, "upload.GetFileRequest") is not None


async def test_a_chat_specific_slow_mode_wait_arms_nothing(ledger):
    """Peer is excluded from the key on purpose; a per-chat limit is not ours."""
    error = telethon_errors.SlowModeWaitError(request=None)
    error.seconds = 30
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.SlowModeWaitError):
        await client._call(None, messages.SendMessageRequest("p", "hi"))

    assert ledger.cooldown_deadline(ACCOUNT, "messages.SendMessageRequest") is None


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


async def test_siblings_do_not_issue_rpcs_while_a_flood_is_armed(ledger):
    """FloodGate behaviour, re-proved on the governor (plan phase 5).

    ADR-0052 parked sibling upload workers while one slept a shared
    FloodWait out. The governor replaces that: the flood arms a per-type
    cooldown, and every sibling request of that type refuses locally with
    zero RPCs instead of sleeping the same wait again.
    """
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    siblings = [FakeClient() for _ in range(3)]
    for sibling in siblings:
        gate.install(sibling, ledger)
    for sibling in siblings:
        with pytest.raises(RateLimitError):
            await sibling._call(None, history())
        assert sibling.sent == []


async def test_a_flood_alert_fires_once_at_arming(ledger, capsys):
    """m6 review fix: the stderr alert is written exactly when the flood
    arms — the scheduled wakes under the cooldown stay silent."""
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, history())

    alert = capsys.readouterr().err
    assert "telegram flood on messages.GetHistoryRequest" in alert
    assert "cooling for 600s" in alert

    # A later wake that refuses on the armed cooldown (before 50% elapsed)
    # is not an arming event — no new alert.
    fresh = FakeClient()
    gate.install(fresh, ledger)
    with pytest.raises(RateLimitError):
        await fresh._call(None, history())
    assert "cooling for 600s" not in capsys.readouterr().err


async def test_the_raw_api_path_refuses_locally_on_a_gated_type(ledger):
    """G5: `tg api` goes through the governed seam — a cooling type refuses
    with zero RPCs, not a raw send into the penalty."""
    from telethon.tl.functions.users import GetFullUserRequest

    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    client = FakeClient(raises=error)
    gate.install(client, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await client._call(None, GetFullUserRequest(id=42))

    api = FakeClient()
    gate.install(api, ledger)
    with pytest.raises(RateLimitError):
        await api._call(None, GetFullUserRequest(id=42))
    assert api.sent == []


@pytest.mark.parametrize(
    ("rpc_request", "expected_key"),
    [
        (
            messages.GetHistoryRequest(
                peer="p",
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            ),
            "messages.GetHistoryRequest",
        ),
        (
            messages.GetDialogsRequest(
                offset_date=None, offset_id=0, offset_peer="p", limit=100, hash=0
            ),
            "messages.GetDialogsRequest",
        ),
        (messages.SendMessageRequest("p", "hi"), "messages.SendMessageRequest"),
        (
            upload.GetFileRequest(location=None, offset=0, limit=1),
            "upload.GetFileRequest",
        ),
        (messages.GetMessagesRequest(id=[1]), "messages.GetMessagesRequest"),
    ],
)
async def test_every_command_family_refuses_locally_on_its_type(
    ledger, rpc_request, expected_key
):
    """G3: each command family issues a known request type, and a cooldown
    on that exact type refuses locally with zero RPCs — a command can never
    send into a live penalty for the type it is about to issue."""
    error = telethon_errors.FloodWaitError(request=None)
    error.seconds = 600
    source = FakeClient(raises=error)
    gate.install(source, ledger)

    with pytest.raises(telethon_errors.FloodWaitError):
        await source._call(None, rpc_request)

    target = FakeClient()
    gate.install(target, ledger)
    with pytest.raises(RateLimitError) as caught:
        await target._call(None, rpc_request)
    assert caught.value.details["retry_after"] > 0
    assert target.sent == []
    assert set(ledger.active_cooldowns(ACCOUNT)) == {expected_key}
