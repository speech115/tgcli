"""SQLite schema migrations."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tgcli.clone import state, statedb
from tgcli.errors import PolicyError


def _fresh(**overrides) -> state.CloneState:
    s = state.CloneState.new(
        account_user_id=100000001,
        source_peer_id=1234567890,
        source_title="Example Channel",
    )
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _downgrade_to_schema_1(db_path) -> None:
    """Rebuild a live db as the pre-destination-name schema (#175)."""
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE meta DROP COLUMN destination_title")
    conn.execute("ALTER TABLE meta DROP COLUMN destination_username")
    conn.execute("ALTER TABLE meta DROP COLUMN send_intents")
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()


def test_schema_1_database_migrates_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    s.cursor = 4
    s.record_mapping(2, 20)
    state.save(s)
    _downgrade_to_schema_1(state.path_for(s.clone_id))

    loaded = state.load(s.clone_id)

    assert loaded is not None
    assert loaded.cursor == 4
    assert loaded.dest_for(2) == 20
    assert loaded.destination_title is None
    assert loaded.destination_username is None
    assert state.probe(s.clone_id)["schema_version"] == statedb.SCHEMA_VERSION


def test_migrated_database_records_a_destination_name(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    state.save(s)
    _downgrade_to_schema_1(state.path_for(s.clone_id))

    migrated = state.load(s.clone_id)
    assert migrated is not None
    migrated.destination_title = "[Clone] Example Channel"
    migrated.destination_username = "example_clone"
    state.save(migrated)

    reloaded = state.load(s.clone_id)
    assert reloaded is not None
    assert reloaded.destination_title == "[Clone] Example Channel"
    assert reloaded.destination_username == "example_clone"


def test_schema_2_database_gains_an_anchor_map_that_round_trips(tmp_path, monkeypatch):
    """v3 adds the thread-anchor memory; a v2 clone migrates in place and
    keeps its mappings. An album post's anchors share one destination thread,
    so destination ids may repeat."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    s.record_mapping(2, 20)
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("DROP TABLE anchor_map")
    conn.execute("ALTER TABLE meta DROP COLUMN send_intents")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()

    migrated = state.load(s.clone_id)
    assert migrated is not None and migrated.dest_for(2) == 20
    migrated.record_anchor(10, 500)
    migrated.record_anchor(11, 500)
    state.save(migrated)

    reloaded = state.load(s.clone_id)
    assert reloaded is not None
    assert [reloaded.anchor_dest_for(10), reloaded.anchor_dest_for(11)] == [500, 500]
    assert state.probe(s.clone_id)["schema_version"] == statedb.SCHEMA_VERSION


def test_schema_3_database_gains_caption_rest_maps_that_count_in_the_tail(
    tmp_path, monkeypatch
):
    """v4 remembers the messages that carry a split caption's rest; a v3 clone
    migrates in place, and a recorded rest raises the tail the guard expects."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    s.record_mapping(2, 20)
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("DROP TABLE caption_rest_map")
    conn.execute("DROP TABLE discussion_caption_rest_map")
    conn.execute("ALTER TABLE meta DROP COLUMN send_intents")
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    conn.close()

    migrated = state.load(s.clone_id)
    assert migrated is not None and migrated.dest_for(2) == 20
    migrated.record_caption_rest(2, 21)
    state.save(migrated)

    reloaded = state.load(s.clone_id)
    assert reloaded is not None
    assert reloaded.caption_rest_map == {2: 21}
    assert reloaded.max_destination_id() == 21
    assert state.probe(s.clone_id)["schema_version"] == statedb.SCHEMA_VERSION


def test_schema_4_database_gains_send_intents_that_round_trip(tmp_path, monkeypatch):
    """v5 remembers the send each leg is about to make; a v4 clone migrates in
    place with no intent, and a saved intent loads back."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    s.record_mapping(2, 20)
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("ALTER TABLE meta DROP COLUMN send_intents")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()

    migrated = state.load(s.clone_id)
    assert migrated is not None and migrated.send_intents == {}
    migrated.send_intents["posts"] = {"kind": "batch", "sources": [3, 4]}
    state.save(migrated)

    reloaded = state.load(s.clone_id)
    assert reloaded is not None
    assert reloaded.send_intents == {"posts": {"kind": "batch", "sources": [3, 4]}}
    assert state.probe(s.clone_id)["schema_version"] == statedb.SCHEMA_VERSION == 5


@pytest.mark.parametrize(
    "intents",
    [
        '{"posts": {"kind": "batch", "sources": []}}',
        '{"posts": {"kind": "rest", "sources": [1, 2]}}',
        '{"elsewhere": {"kind": "batch", "sources": [1]}}',
        '{"posts": {"kind": "batch", "sources": ["1"]}}',
        '["posts"]',
        "not json",
    ],
)
def test_impossible_send_intent_is_refused(tmp_path, monkeypatch, intents):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("UPDATE meta SET send_intents = ?", (intents,))
    conn.commit()
    conn.close()

    with pytest.raises(PolicyError, match="manual repair"):
        state.load(s.clone_id)


def test_newer_schema_version_is_refused(tmp_path, monkeypatch):
    """Downgrade is not a migration: a future db must not be silently read."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute(f"PRAGMA user_version={statedb.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(PolicyError, match="unsupported schema version"):
        state.load(s.clone_id)


def test_status_reports_schema_version_and_integrity(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    from tgcli.cli import main

    code = main(["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    entry = payload["clones"][0]
    assert entry["schema_version"] == statedb.SCHEMA_VERSION
    assert entry["integrity"] == "ok"


def test_status_reports_integrity_for_truncated_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    path.write_bytes(path.read_bytes()[:40])
    from tgcli.cli import main

    code = main(["clone", "status", "--json"])
    assert code == 0
    entry = json.loads(capsys.readouterr().out)["clones"][0]
    assert entry["unreadable"] is True
    assert entry["integrity"] != "ok"
    assert entry["schema_version"] is None


def test_status_reports_wrong_user_version(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()
    from tgcli.cli import main

    code = main(["clone", "status", "--json"])
    assert code == 0
    entry = json.loads(capsys.readouterr().out)["clones"][0]
    assert entry["unreadable"] is True
    assert "schema version" in entry["integrity"]


def _snapshot(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
