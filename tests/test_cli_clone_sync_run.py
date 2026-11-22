"""clone sync: destination tail guards, limits, flood waits, progress,
pins, rosters, max runtime and the run lock."""

import json
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneCommentsClient,
    CloneForumClient,
    CloneSyncClient,
    anchor,
    channel,
    message,
    seed_clone,
    seed_comments_clone,
    topic_create,
)
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state


def test_clone_sync_blocks_unexpected_destination_tail_before_copy(
    config_env, monkeypatch, capsys
):
    seed_clone()
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 2
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert error["unexpected"] == 1
    assert "unexpected tail" in error["message"]
    assert client.requests == []
    assert client.iter_messages_calls == []
    assert not safety.audit_path().exists()


def test_clone_sync_blocks_when_recorded_tail_is_missing(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 5)
    clone_state.cursor = 1
    state.save(clone_state)
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 4
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "recorded tail is missing" in capsys.readouterr().err
    assert client.requests == []
    assert client.iter_messages_calls == []
    assert not safety.audit_path().exists()


def test_clone_sync_rejects_recorded_forum_clone_with_nonforum_destination(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneSyncClient([message(2)])
    client.source = channel(
        123, "Forum chat", broadcast=False, megagroup=True, forum=True
    )
    client.destination = channel(
        999,
        "Forum chat",
        creator=True,
        broadcast=False,
        megagroup=True,
        forum=False,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "not a private owned forum megagroup" in capsys.readouterr().err
    assert client.requests == []
    assert client.iter_messages_calls == []


def test_clone_sync_blocks_unmapped_topic_create_tail_before_retrying_create(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")

    class AcceptedWithoutConfirmationClient(CloneForumClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.CreateForumTopicRequest):
                self.requests.append(request)
                self.destination_last_id += 1
                self.destination_actions[self.destination_last_id] = (
                    types.MessageActionTopicCreate(title=request.title, icon_color=0)
                )
                return SimpleNamespace(updates=[])
            return await super().__call__(request)

    client = AcceptedWithoutConfirmationClient([topic_create(2, "News")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "did not confirm" in capsys.readouterr().err
    assert len(client.requests) == 1
    assert state.load(clone_state.clone_id).topic_map == {}

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "unexpected tail" in capsys.readouterr().err
    assert len(client.requests) == 1
    assert client.iter_messages_calls == [("posts", 0)]
    assert state.load(clone_state.clone_id).topic_map == {}


def test_clone_sync_accepts_mapped_topic_create_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 2)
    state.save(clone_state)
    client = CloneForumClient([message(3)])
    client.destination_last_id = 3
    client.destination_actions[2] = types.MessageActionTopicCreate(
        title="News", icon_color=0
    )
    client.destination_actions[3] = object()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.ChannelPrivateError,
        telethon_errors.ChannelInvalidError,
        telethon_errors.ChatForbiddenError,
    ],
)
def test_clone_sync_unreachable_destination_exits_2(
    config_env, monkeypatch, capsys, error
):
    """A destination the account can no longer open — deleted, left, banned —
    is the documented policy failure, not a raw Telethon traceback."""
    seed_clone()

    class GoneDestinationClient(CloneSyncClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel):
                raise error(request=None)
            return await super().get_entity(ref)

    client = GoneDestinationClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "clone destination is unavailable" in capsys.readouterr().err
    assert client.requests == []


def progress_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("[sync ")]


def test_sync_survives_an_unavailable_approximate_total(
    config_env, monkeypatch, capsys
):
    """A best-effort total never fails the sync — the line degrades to `~?`."""

    class NoTotalClient(CloneSyncClient):
        async def get_messages(self, entity, limit=None):
            if entity is self.source:
                raise telethon_errors.ChatAdminRequiredError(request=None)
            return await super().get_messages(entity, limit=limit)

    seed_clone()
    client = NoTotalClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    assert "[sync 123] 1/~? · forwarded" in progress_lines(capsys.readouterr().err)


def test_sync_flood_wait_on_the_approximate_total_exits_5(
    config_env, monkeypatch, capsys
):
    """The best-effort total must never swallow FloodWait."""
    seed_clone()

    class FloodingTotalClient(CloneSyncClient):
        async def get_messages(self, entity, limit=None):
            if entity is self.source:
                raise telethon_errors.FloodWaitError(request=None, capture=61)
            return await super().get_messages(entity, limit=limit)

    client = FloodingTotalClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    error = json.loads(capsys.readouterr().err.splitlines()[-1])["error"]
    assert error["code"] == "FLOOD_WAIT"
    assert error["retry_after"] == 61
    assert client.requests == []


def test_completing_sync_pins_mapped_source_and_reports_set(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(12, 9)
    clone_state.cursor = 12
    state.save(clone_state)
    client = CloneSyncClient([])
    client.source_pinned_msg_id = 12
    client.destination_pinned_msg_id = None
    client.destination_last_id = 9
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["pinned"] == {
        "source_id": 12,
        "destination_id": 9,
        "status": "set",
    }
    pins = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.UpdatePinnedMessageRequest)
    ]
    assert len(pins) == 1
    assert pins[0].id == 9
    assert pins[0].silent is True
    assert isinstance(pins[0].peer, types.InputPeerChannel)
    assert pins[0].peer.channel_id == 999
    assert state.load(clone_state.clone_id).pinned_dest_id == 9


class RosterClient(CloneSyncClient):
    """Source whose roster is readable; records each listing."""

    def __init__(self, messages, *, on_participant=None):
        super().__init__(messages)
        self.iter_participants_calls = []
        self.on_participant = on_participant

    async def iter_participants(self, entity, limit=None):
        self.iter_participants_calls.append(entity)
        for member_id in (701, 702, 703):
            if self.on_participant is not None:
                self.on_participant()
            yield SimpleNamespace(
                id=member_id,
                username=None,
                first_name="Member",
                last_name=None,
                phone=None,
                bot=False,
            )


def test_clone_sync_cap_hit_in_copy_skips_pin_and_roster(
    config_env, monkeypatch, capsys
):
    """A run the cap stops early is a run that stops early: CONTRACT says it
    never calls the pin phase, and the roster listing is deferred, not run
    past the cap."""
    from tgcli import pace
    from tgcli.clone import roster

    monkeypatch.setattr(pace, "remaining", lambda: 0.0)
    clone_state = seed_clone()
    sidecar = roster.path_for(clone_state.clone_id)
    sidecar.write_text('{"peer": "source", "id": 1}\n')
    client = RosterClient([message(2), message(3)])
    client.source_pinned_msg_id = 2
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "0.001", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["remaining"] is True
    assert data["stop_reason"] == "wall_clock_cap"
    assert client.full_requests == []
    assert not any(
        isinstance(item, functions.messages.UpdatePinnedMessageRequest)
        for item in client.requests
    )
    assert data["sync"]["pinned"]["status"] == "unmapped"
    assert client.iter_participants_calls == []
    assert data["sync"]["participants"]["source"] == {
        "peer_id": 123,
        "status": "deferred",
        "count": 0,
        "reason": "wall_clock_cap",
    }
    assert sidecar.read_text() == '{"peer": "source", "id": 1}\n'


def test_clone_sync_cap_hit_in_roster_defers_roster_and_reports_done(
    config_env, monkeypatch, capsys
):
    """Every post copied, then the cap runs out inside the participant
    listing: the listing stops at the cap and the run is not reported as
    having copy work remaining."""
    from tgcli import pace

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pace, "remaining", lambda: cap["remaining"])
    seed_clone()

    def exhaust():
        cap["remaining"] = 0.0

    client = RosterClient([message(2), message(3)], on_participant=exhaust)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "100", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["sync"]["copied"] == 2
    assert data["sync"]["more"] is False
    assert data["remaining"] is False
    assert "stop_reason" not in data
    assert data["sync"]["participants"]["source"]["status"] == "deferred"
    assert data["sync"]["participants"]["source"]["reason"] == "wall_clock_cap"
    assert data["sync"]["participants"]["source"]["count"] == 0


def test_clone_sync_cap_hit_in_source_roster_defers_discussion_roster(
    config_env, monkeypatch, capsys
):
    """Once the cap runs out while listing the source, the discussion group
    is deferred too, never resolved or listed."""
    from tgcli import pace

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pace, "remaining", lambda: cap["remaining"])
    seed_comments_clone()

    class CappedRosterClient(CloneCommentsClient):
        def __init__(self):
            super().__init__([])
            self.iter_participants_calls = []

        async def iter_participants(self, entity, limit=None):
            self.iter_participants_calls.append(entity)
            cap["remaining"] = 0.0
            yield self.group_members[0]

    client = CappedRosterClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "100", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    participants = data["sync"]["participants"]
    assert client.iter_participants_calls == [client.source]
    assert participants["source"]["status"] == "deferred"
    assert participants["discussion"] == {
        "peer_id": 55,
        "status": "deferred",
        "count": 0,
        "reason": "wall_clock_cap",
    }


def test_sync_resolves_a_bare_title_through_clone_state(
    config_env, monkeypatch, capsys
):
    """#171: a title is not something Telegram resolves.

    Telethon falls back to its session entity cache and can answer with a
    stale access hash, which the server rejects as CHANNEL_PRIVATE. The
    clone's own state already records the peer id under that title.
    """
    seed_clone()

    class TitleClient(CloneSyncClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel) and ref.channel_id == 123:
                return self.source
            if isinstance(ref, str):
                raise AssertionError(f"title must not reach Telegram: {ref!r}")
            return await super().get_entity(ref)

    client = TitleClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "Source channel", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1


def test_sync_title_matching_two_clones_is_blocked(config_env, monkeypatch, capsys):
    seed_clone(title="Source channel")
    other = state.CloneState.new(
        account_user_id=42,
        source_peer_id=124,
        source_title="Source channel copy",
    )
    other.destination_peer_id = 998
    state.save(other)
    make_session_fake(monkeypatch, CloneSyncClient([message(2)]))

    assert main(["clone", "sync", "Source channel", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "BLOCKED"
    assert "matched 2 clones" in payload["error"]["message"]


def test_clone_sync_cap_hit_in_comments_leg_stops_the_leg(
    config_env, monkeypatch, capsys
):
    """The comments leg honours --max-runtime like the posts leg: once the cap
    runs out mid-leg, no further comment is copied, the pin phase never runs,
    the roster is deferred, and the run reports the stop with work left."""
    from tgcli import pace

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pace, "remaining", lambda: cap["remaining"])
    seed_comments_clone()

    def comment(message_id):
        return message(
            message_id,
            chat=55,
            message=f"c{message_id}",
            reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
        )

    class CapInCommentsClient(CloneCommentsClient):
        async def __call__(self, request):
            result = await super().__call__(request)
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            if peer is self.destination_group and isinstance(
                request,
                (
                    functions.messages.SendMessageRequest,
                    functions.messages.ForwardMessagesRequest,
                ),
            ):
                cap["remaining"] = 0.0
            return result

    client = CapInCommentsClient(
        [message(2)], [anchor(10, 2), comment(12), comment(13), comment(14)]
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "100", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["sync"]["copied"] == 2
    assert data["sync"]["discussion_cursor"] == 12
    assert data["remaining"] is True
    assert data["stop_reason"] == "wall_clock_cap"
    assert client.full_requests == []
    assert data["sync"]["participants"]["source"]["reason"] == "wall_clock_cap"


def test_clone_sync_with_comments_reads_nothing_once_the_cap_is_spent(
    config_env, monkeypatch, capsys
):
    """A run that starts with no --max-runtime left sends no history read."""
    from tgcli import pace

    monkeypatch.setattr(pace, "remaining", lambda: 0.0)
    seed_comments_clone()
    client = CloneCommentsClient([message(2)], [anchor(10, 2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "0.001", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert data["sync"]["copied"] == 0
    assert client.iter_messages_calls == []
