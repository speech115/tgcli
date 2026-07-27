"""JSON → SQLite one-time import and export-state (ADR-0060 slice 2)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tgcli.clone import state
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


def test_json_imports_exactly_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.cursor = 7
    s.record_mapping(1, 10)
    payload = json.dumps(s.to_dict(), ensure_ascii=False)
    json_path = state.json_path_for(s.clone_id)
    json_path.parent.mkdir(parents=True)
    json_path.write_text(payload)

    loaded = state.load(s.clone_id)
    err = capsys.readouterr().err
    assert loaded is not None
    assert loaded.cursor == 7
    assert loaded.dest_for(1) == 10
    assert state.path_for(s.clone_id).is_file()
    imported = json_path.with_name(json_path.name + ".imported")
    assert imported.is_file()
    assert imported.read_text() == payload
    assert not json_path.exists()
    assert "imported clone state" in err

    # Second load must not re-import or touch the backup.
    before = imported.read_bytes()
    again = state.load(s.clone_id)
    assert again is not None
    assert again.to_dict() == loaded.to_dict()
    assert imported.read_bytes() == before
    assert "imported clone state" not in capsys.readouterr().err


def test_both_db_and_json_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    state.json_path_for(s.clone_id).write_text(json.dumps(s.to_dict()))

    with pytest.raises(PolicyError, match="both"):
        state.load(s.clone_id)


def test_corrupted_json_keeps_today_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = state.json_path_for("f" * 64)
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")

    with pytest.raises(PolicyError, match="corrupted"):
        state.load("f" * 64)


def test_export_state_prints_v2_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 99
    s.cursor = 3
    s.record_mapping(2, 20)
    state.save(s)

    from tgcli.cli import main

    code = main(["clone", "export-state", str(s.source_peer_id), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)
    assert payload == s.to_dict()
    # Rollback proof: the export re-validates through from_dict.
    assert state.CloneState.from_dict(payload).to_dict() == s.to_dict()


def test_export_state_unknown_clone_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    from tgcli.cli import main

    code = main(["clone", "export-state", "999999999", "--json"])
    assert code == 2


def test_status_reports_schema_version_and_integrity(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    from tgcli.cli import main

    code = main(["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    entry = payload["clones"][0]
    assert entry["schema_version"] == 1
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
