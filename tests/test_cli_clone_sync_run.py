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
    CloneReuploadClient,
    CloneSyncClient,
    anchor,
    channel,
    message,
    seed_clone,
    seed_comments_clone,
    topic_create,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli import safety, session
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


def test_clone_sync_accepts_service_only_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 2
    client.destination_actions[2] = object()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["copied"] == 1
    assert state.load(clone_state.clone_id).dest_for(2) == 3


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


def test_clone_sync_limit_reports_more_and_next_run_resumes(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(4), message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "2", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)["sync"]
    assert first["copied"] == 2
    assert first["cursor"] == 3
    assert first["more"] is True
    assert [request.id for request in client.requests] == [[2], [3]]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["sync"]
    assert second["copied"] == 1
    assert second["cursor"] == 4
    assert second["more"] is False
    assert [request.id for request in client.requests] == [[2], [3], [4]]
    assert state.load(clone_state.clone_id).dest_for(4) == 4


def test_clone_sync_flood_wait_persists_cooldown_without_advancing(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class FloodClient(CloneSyncClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=request, capture=600)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.dest_for(2) is None
    assert len(client.requests) == 1
    assert client.session_mutation_safe is True


def test_clone_sync_flood_wait_exits_5_without_advancing(
    config_env, monkeypatch, capsys
):
    """A flood exits 5 locally; the governor's seam arms the per-type
    cooldown (covered by the governor unit tests) and the cursor stays put."""
    seed_clone()
    other = state.CloneState.new(
        account_user_id=42,
        source_peer_id=456,
        source_title="Other channel",
        source_kind="broadcast",
    )
    other.destination_peer_id = 888
    state.save(other)

    class FloodClient(CloneSyncClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=request, capture=600)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    assert len(client.requests) == 1
    err = json.loads(capsys.readouterr().err.splitlines()[-1])["error"]
    assert err["retry_after"] == 600


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


def test_clone_sync_readonly_blocks_before_config_or_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["--readonly", "clone", "sync", "@source"]) == 2
    assert not safety.audit_path().exists()


def test_clone_sync_unmatched_confirmation_does_not_advance_state(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class UnmatchedClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates[0].random_id += 1
            return response

    client = UnmatchedClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "did not confirm" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}
    assert client.destination_last_id == 2


def test_clone_sync_plain_output_has_contract_columns(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--plain"]) == 0

    assert capsys.readouterr().out.rstrip().split("\t") == [
        "1",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "2",
        clone_state.clone_id,
        "123",
        "999",
        "False",
        "0",
        "0",
        "0",
    ]


def progress_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("[sync ")]


def test_sync_reports_batch_progress_on_stderr(config_env, monkeypatch, capsys):
    """ADR-0049: a batch line names the running count and its transport."""
    seed_clone()
    client = CloneSyncClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    lines = progress_lines(capsys.readouterr().err)
    assert lines[-2] == "[sync 123] 2/~2 · forwarded"
    # The roster leg copies participants, not messages: its own counters
    # start empty rather than echoing the posts leg's (#174).
    assert lines[-1] == "[sync 123] 0/~? · roster"


def test_sync_progress_counts_continue_from_earlier_runs(
    config_env, monkeypatch, capsys
):
    """The count is progress against the source, not against this run."""
    clone_state = seed_clone()
    clone_state.record_mapping(2, 1)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneSyncClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    assert "[sync 123] 2/~2 · forwarded" in progress_lines(capsys.readouterr().err)


def test_sync_json_mode_emits_progress_without_touching_stdout(
    config_env, monkeypatch, capsys
):
    """ADR-0049: --json gets the same stderr lines; stdout stays one document."""
    seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["sync"]["copied"] == 1
    assert "[sync " not in captured.out
    assert progress_lines(captured.err)


def test_sync_announces_the_comments_and_roster_phases(config_env, monkeypatch, capsys):
    seed_comments_clone()

    class CountingCommentsClient(CloneCommentsClient):
        def __init__(self, messages, comments=()):
            super().__init__(messages, comments)
            self.total_requests = 0

        async def get_messages(self, entity, limit=None, ids=None):
            if entity is self.source and limit == 0:
                self.total_requests += 1
            return await super().get_messages(entity, limit=limit, ids=ids)

    client = CountingCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            message(
                13,
                message="also",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    lines = progress_lines(capsys.readouterr().err)
    # Each leg counts its own work against its own source (#174): the posts
    # leg against the channel (1 post), the comments leg against the
    # discussion group (3 items). The batch after the phase line must show a
    # resolved denominator, not the stuck `~?` of a stale `_total_resolved`.
    assert lines[0] == "[sync 123] 0/~? · posts"
    comments_idx = lines.index("[sync 123] 0/~? · comments")
    assert lines[comments_idx - 1] == "[sync 123] 1/~1 · forwarded"
    assert lines[comments_idx + 1] == "[sync 123] 1/~3 · reuploaded"
    assert lines[comments_idx + 2] == "[sync 123] 2/~3 · reuploaded"
    assert lines[-1] == "[sync 123] 0/~? · roster"
    # The posts leg resolves once per window (limit=0); the comments leg asks
    # the discussion group, never the channel.
    assert client.total_requests == 1


def test_sync_reports_transfer_bytes_while_reuploading(config_env, monkeypatch, capsys):
    """The reupload legs report through the shared transfer callback."""
    from tgcli.clone import progress as clone_progress

    monkeypatch.setattr(clone_progress, "PROGRESS_EVERY_BYTES", 0)
    seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient([message(2, media=photo)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    uploads = [line for line in progress_lines(capsys.readouterr().err) if "·" in line]
    assert any(
        line.startswith("[sync 123] 0/~1 · reupload · message-2 · upload ")
        and line.endswith(" MB (100%)")
        for line in uploads
    )


def test_sync_progress_lines_carry_no_control_characters(
    config_env, monkeypatch, capsys
):
    """ADR-0049: plain lines only — safe in a log file and an agent transcript."""
    seed_clone()
    client = CloneSyncClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source"]) == 0

    lines = progress_lines(capsys.readouterr().err)
    assert lines
    for line in lines:
        assert "\r" not in line
        assert "\x1b[" not in line


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


def test_sync_error_stays_the_final_stderr_line_after_progress(
    config_env, monkeypatch, capsys
):
    """CONTRACT §2: progress lines never displace the single-line error JSON."""
    seed_clone()

    class FloodingClient(CloneSyncClient):
        async def __call__(self, request):
            if len(self.requests) >= 1:
                raise telethon_errors.FloodWaitError(request=None, capture=61)
            return await super().__call__(request)

    client = FloodingClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert progress_lines(captured.err), "progress must precede the error"
    error = json.loads(lines[-1])["error"]
    assert error["code"] == "FLOOD_WAIT"
    assert error["retry_after"] == 61


def test_sync_with_nothing_to_copy_asks_for_no_approximate_total(
    config_env, monkeypatch, capsys
):
    """ADR-0045: an idle keep-up-to-date sync must not spend an extra RPC."""
    clone_state = seed_clone()
    clone_state.record_mapping(2, 1)
    clone_state.cursor = 2
    state.save(clone_state)

    class CountingClient(CloneSyncClient):
        def __init__(self, messages):
            super().__init__(messages)
            self.total_requests = 0

        async def get_messages(self, entity, limit=None):
            if entity is self.source:
                self.total_requests += 1
            return await super().get_messages(entity, limit=limit)

    client = CountingClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 0
    assert client.total_requests == 0


def test_sync_flood_wait_on_the_approximate_total_arms_the_cooldown(
    config_env, monkeypatch, capsys
):
    """The best-effort total must never swallow FloodWait (ADR-0072)."""
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


def test_early_stop_sync_skips_pin_phase_but_reports_snapshot(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    state.save(clone_state)
    client = CloneSyncClient([message(1), message(2), message(3)])
    client.source_pinned_msg_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "1", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["more"] is True
    assert sync["pinned"] == {
        "source_id": None,
        "destination_id": None,
        "status": "unmapped",
    }
    assert not any(
        isinstance(
            item,
            (
                functions.channels.GetFullChannelRequest,
                functions.messages.UpdatePinnedMessageRequest,
            ),
        )
        for item in [*client.requests, *client.full_requests]
    )


def test_forum_sync_omits_pinned_key(config_env, monkeypatch, capsys):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneForumClient([])
    client.destination_last_id = 1002
    client.destination_actions[1002] = types.MessageActionTopicCreate(
        title="General", icon_color=0
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert "pinned" not in sync
    assert not any(
        isinstance(item, functions.messages.UpdatePinnedMessageRequest)
        for item in client.requests
    )


def test_sync_short_flood_wait_exits_5_without_foreground_retry(
    config_env, monkeypatch, capsys
):
    """ADR-0072 decision 6: no foreground short-wait retry — the governor arms
    the cooldown and the run exits 5; the schedule resumes it later."""
    seed_clone()

    class FloodClient(CloneSyncClient):
        def __init__(self, messages):
            super().__init__(messages)

        async def __call__(self, request):
            raise telethon_errors.FloodWaitError(request=None, capture=3)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error"]["retry_after"] == 3
    assert "flood wait: retrying" not in captured.err


def test_clone_sync_max_runtime_stops_normally_with_resume(
    config_env, monkeypatch, capsys
):
    """M5 review fix: --max-runtime bounds clone sync as a normal stop."""
    from tgcli.governor import pacing

    # Deterministic: the cap is always exhausted at the first check, whatever
    # the real wall clock says (`main` re-arms the runtime from the flag).
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)
    seed_clone()
    client = CloneSyncClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "0.001", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert "resume" in data


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
    from tgcli.clone import roster
    from tgcli.governor import pacing

    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)
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
    from tgcli.governor import pacing

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: cap["remaining"])
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


def test_clone_sync_cap_spent_by_pin_phase_skips_roster(
    config_env, monkeypatch, capsys
):
    """The pin phase can spend the rest of the cap; the roster listing must
    not start after it."""
    from tgcli.governor import pacing

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: cap["remaining"])
    seed_clone()

    class SlowPinClient(RosterClient):
        async def __call__(self, request):
            if isinstance(request, functions.channels.GetFullChannelRequest):
                cap["remaining"] = 0.0
            return await super().__call__(request)

    client = SlowPinClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "100", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["sync"]["copied"] == 2
    assert data["remaining"] is False
    assert client.full_requests
    assert client.iter_participants_calls == []
    assert data["sync"]["participants"]["source"]["status"] == "deferred"
    assert data["sync"]["participants"]["source"]["reason"] == "wall_clock_cap"


def test_clone_sync_cap_hit_in_source_roster_defers_discussion_roster(
    config_env, monkeypatch, capsys
):
    """Once the cap runs out while listing the source, the discussion group
    is deferred too, never resolved or listed."""
    from tgcli.governor import pacing

    cap = {"remaining": 100.0}
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: cap["remaining"])
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


def test_sync_unreachable_source_is_not_found_not_a_raw_rpc_error(
    config_env, monkeypatch, capsys
):
    """#171: `ChannelPrivate` blamed the operator's access for a resolve bug."""
    seed_clone()

    class PrivateClient(CloneSyncClient):
        async def get_entity(self, ref):
            raise telethon_errors.ChannelPrivateError(request=None)

    make_session_fake(monkeypatch, PrivateClient([message(2)]))

    assert main(["clone", "sync", "@source", "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "NOT_FOUND"
    assert "clone source" in payload["error"]["message"]


def test_sync_names_the_recovery_command_for_an_unlinked_discussion(
    config_env, monkeypatch, capsys
):
    """#170: 're-run clone init' left the operator to guess the invocation."""
    clone_state = seed_clone()
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 777
    state.save(clone_state)
    make_session_fake(monkeypatch, CloneSyncClient([message(2)]))

    assert main(["clone", "sync", "@source", "--json"]) == 2
    message_text = json.loads(capsys.readouterr().out)["error"]["message"]
    assert "tg clone init @source" in message_text


def _hold_clone_lock(clone_id):
    import fcntl

    state.clones_dir().mkdir(parents=True, exist_ok=True)
    holder = (state.clones_dir() / f"{clone_id}.lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return holder


def test_sync_refuses_while_another_run_holds_the_clone(
    config_env, monkeypatch, capsys
):
    """The session lock is per session file, so a `--session-role job` sync
    and a primary-session sync of the same clone used to run at once and
    post every message twice. The clone slot now has its own lock."""
    clone_state = seed_clone()
    client = CloneSyncClient([message(2), message(3)])
    make_session_fake(monkeypatch, client)

    holder = _hold_clone_lock(clone_state.clone_id)
    try:
        assert main(["clone", "sync", "@source", "--json"]) == 2
    finally:
        holder.close()

    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "BLOCKED"
    assert error["message"] == (
        f"clone {clone_state.clone_id} is busy: another clone sync or init "
        "is running; retry when it finishes"
    )
    assert not any(
        isinstance(
            request,
            (
                functions.messages.SendMessageRequest,
                functions.messages.ForwardMessagesRequest,
            ),
        )
        for request in client.requests
    )
    assert state.load(clone_state.clone_id).cursor == 0

    # Released by the holder, the same clone syncs, and leaves no lock held.
    assert main(["clone", "sync", "@source", "--json"]) == 0
    assert state.load(clone_state.clone_id).cursor == 3
    _hold_clone_lock(clone_state.clone_id).close()
