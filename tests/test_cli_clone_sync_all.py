"""`clone sync --all`: every clone of the account in one run."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from tests.conftest import make_session_fake
from tests.fakes.clone import CloneSyncClient, message, seed_clone
from tests.fakes.clone_env import config_env as config_env
from tgcli.cli import main
from tgcli.clone import state


def _seed_unreachable_clone() -> state.CloneState:
    """A second clone whose source this account can no longer resolve."""
    other = state.CloneState.new(
        account_user_id=42, source_peer_id=456, source_title="Gone channel"
    )
    other.destination_peer_id = 998
    state.save(other)
    return other


def test_sync_all_copies_every_clone_and_reruns_idle_from_one_batched_read(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(3), message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "--all", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    [entry] = result["clones"]
    assert entry["source"] == "-1000000000123"
    assert entry["title"] == "Source channel"
    assert entry["sync"]["copied"] == 2
    assert result["deferred"] == []
    assert state.load(clone_state.clone_id).cursor == 3

    assert main(["clone", "sync", "--all", "--json"]) == 0

    rerun = json.loads(capsys.readouterr().out)
    assert rerun["clones"][0]["sync"]["up_to_date"] is True
    assert len(client.peer_dialog_requests) == 2
    assert client.newest_requests == []


def test_sync_all_refuses_every_clone_while_one_remembers_a_flood(
    config_env, monkeypatch, capsys
):
    """A FloodWait belongs to the account: no clone of it asks Telegram
    anything until the remembered wait is over."""
    clone_state = seed_clone()
    clone_state.retry_not_before = (
        datetime.now(UTC) + timedelta(seconds=120)
    ).isoformat()
    state.save(clone_state)
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "--all", "--json"]) == 5

    error = json.loads(capsys.readouterr().err)["error"]
    assert 0 < error["retry_after"] <= 120
    assert client.requests == [] and client.peer_dialog_requests == []


def test_a_clone_that_refuses_does_not_stop_the_others(config_env, monkeypatch, capsys):
    good = seed_clone()
    gone = _seed_unreachable_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    code = main(["clone", "sync", "--all", "--json"])

    result = json.loads(capsys.readouterr().out)
    by_id = {entry["id"]: entry for entry in result["clones"]}
    assert set(by_id) == {good.clone_id, gone.clone_id}
    assert by_id[good.clone_id]["sync"]["copied"] == 1
    assert by_id[gone.clone_id]["error"]["code"] in {"NOT_FOUND", "BLOCKED"}
    assert code in {2, 4}
    assert state.load(good.clone_id).cursor == 2


def test_sync_takes_a_source_or_all_but_not_both(config_env, capsys):
    assert main(["clone", "sync", "@source", "--all", "--json"]) != 0
    assert "not allowed with" in capsys.readouterr().err
    assert main(["clone", "sync", "--json"]) != 0
    assert "required" in capsys.readouterr().err
