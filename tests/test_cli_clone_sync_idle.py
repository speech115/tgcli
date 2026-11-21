"""clone sync: ending early when nothing is new, and remembering a long flood
in the clone."""

import json
import os
import time

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneCommentsClient,
    CloneSyncClient,
    anchor,
    message,
    seed_clone,
    seed_comments_clone,
)
from tests.fakes.clone_env import config_env as config_env
from tgcli.cli import main
from tgcli.clone import roster, state

DAY = 24 * 60 * 60


def fresh_roster(clone_state, *, age=60):
    path = roster.path_for(clone_state.clone_id)
    state.ensure_clones_dir()
    path.write_text("")
    then = time.time() - age
    os.utime(path, (then, then))


def caught_up(*, cursor=2):
    clone_state = seed_clone()
    clone_state.cursor = cursor
    state.save(clone_state)
    fresh_roster(clone_state)
    return clone_state


class QuietClient(CloneSyncClient):
    """Counts everything the fake Telegram is asked beyond the idle probe."""

    def __init__(self, messages):
        super().__init__(messages)
        self.asked = []

    async def get_entity(self, ref):
        self.asked.append("get_entity")
        return await super().get_entity(ref)

    async def get_messages(self, entity, limit=None):
        self.asked.append("get_messages")
        return await super().get_messages(entity, limit=limit)

    async def iter_messages(self, entity, **kwargs):
        self.asked.append("iter_messages")
        async for item in super().iter_messages(entity, **kwargs):
            yield item

    async def iter_participants(self, entity, limit=None):
        self.asked.append("iter_participants")
        async for item in super().iter_participants(entity, limit=limit):
            yield item


def newest_reads(client):
    return [request.to_dict() for request in client.newest_requests]


def test_a_sync_with_nothing_past_the_cursor_ends_after_one_history_read(
    config_env, monkeypatch, capsys
):
    clone_state = caught_up()
    client = QuietClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["remaining"] is False
    assert result["sync"]["up_to_date"] is True
    assert result["sync"]["copied"] == 0
    assert result["sync"]["more"] is False
    assert result["sync"]["cursor"] == 2
    assert result["sync"]["pinned"]["status"] == "unmapped"
    assert result["sync"]["participants"]["source"] == {
        "peer_id": 123,
        "status": "fresh",
        "count": 0,
        "reason": "collected_within_24h",
    }
    assert result["clone"]["destination"] == {"id": 999, "title": None}
    assert newest_reads(client) == [
        functions.messages.GetHistoryRequest(
            peer=types.InputPeerChannel(channel_id=123, access_hash=1),
            offset_id=0,
            offset_date=None,
            add_offset=0,
            limit=1,
            max_id=0,
            min_id=0,
            hash=0,
        ).to_dict()
    ]
    assert client.asked == []
    assert client.requests == [] and client.full_requests == []
    assert state.load(clone_state.clone_id).last_synced_at is not None


def test_a_sync_that_copies_leaves_a_roster_under_a_day_old_alone(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    fresh_roster(clone_state, age=DAY - 600)
    client = QuietClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["participants"]["source"]["status"] == "fresh"
    assert "iter_participants" not in client.asked
    assert client.asked.count("iter_messages") == 1


def test_idle_needs_the_discussion_group_to_be_caught_up_too(
    config_env, monkeypatch, capsys
):
    clone_state = seed_comments_clone(cursor=2, discussion_cursor=10)
    fresh_roster(clone_state)
    client = CloneCommentsClient(
        [message(2)], [anchor(10, 2), message(11, message="a comment")]
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["up_to_date"] is False
    assert sync["discussion_cursor"] == 11
    assert [request.peer.channel_id for request in client.newest_requests] == [
        123,
        55,
    ]


def test_a_clone_whose_comments_are_not_linked_is_refused_not_idle(
    config_env, monkeypatch, capsys
):
    clone_state = seed_comments_clone(cursor=2, discussion_linked=False)
    clone_state.discussion_destination_peer_id = None
    state.save(clone_state)
    fresh_roster(clone_state)
    client = CloneCommentsClient([message(2)], [])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "not linked yet" in capsys.readouterr().err


def test_an_uninitialized_clone_is_still_refused(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, QuietClient([message(2)]))

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "not initialized" in capsys.readouterr().err


def at(monkeypatch, moment):
    monkeypatch.setattr(state, "_now", lambda: moment)


def test_the_wait_is_refused_even_for_a_source_named_by_title(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.remember_flood(90)
    state.save(clone_state)
    quiet = QuietClient([message(2)])
    make_session_fake(monkeypatch, quiet)

    assert main(["clone", "sync", "Source channel", "--json"]) == 5

    assert 0 < json.loads(capsys.readouterr().err)["error"]["retry_after"] <= 90
    assert quiet.asked == [] and quiet.requests == []


class FloodedProbe(CloneSyncClient):
    async def __call__(self, request):
        if isinstance(request, functions.messages.GetHistoryRequest):
            raise telethon_errors.FloodWaitError(request=request, capture=45)
        return await super().__call__(request)


def test_a_flood_met_by_the_idle_read_is_remembered(config_env, monkeypatch, capsys):
    clone_state = caught_up()
    make_session_fake(monkeypatch, FloodedProbe([message(2)]))

    assert main(["clone", "sync", "@source", "--json"]) == 5

    assert json.loads(capsys.readouterr().err)["error"]["retry_after"] == 45
    assert state.load(clone_state.clone_id).flood_left() in range(44, 46)


class SessionWithoutSelfHash(CloneSyncClient):
    """Telethon knows the account id but cannot build its input peer."""

    async def get_me(self, input_peer=False):
        if input_peer:
            raise AttributeError("'NoneType' object has no attribute '_as_input_peer'")
        return await super().get_me()


def test_a_session_that_cannot_name_its_account_cheaply_still_syncs(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    make_session_fake(monkeypatch, SessionWithoutSelfHash([message(2)]))

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    assert state.load(clone_state.clone_id).dest_for(2) is not None
