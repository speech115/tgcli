import json
import stat
from datetime import UTC, datetime

import pytest

from tgcli.clone import state
from tgcli.errors import PolicyError


def _fresh() -> state.CloneState:
    return state.CloneState.new(
        account_user_id=100000001,
        source_peer_id=1234567890,
        source_title="Example Channel",
    )


def test_new_clone_has_expected_defaults():
    s = _fresh()
    assert s.version == state.VERSION
    assert s.source_kind == "broadcast"
    assert s.destination_peer_id is None
    assert s.cursor == 0
    assert s.id_map == {}
    assert s.retry_not_before is None
    assert s.clone_id == state.clone_id(100000001, 1234567890)


def test_save_then_load_round_trip():
    s = _fresh()
    s.source_kind = "megagroup"
    s.destination_peer_id = 1987654321
    s.cursor = 42
    s.record_mapping(7, 3)
    state.save(s)

    loaded = state.load(s.clone_id)
    assert loaded is not None
    assert loaded.destination_peer_id == 1987654321
    assert loaded.cursor == 42
    assert loaded.dest_for(7) == 3
    assert loaded.source_title == "Example Channel"
    assert loaded.source_kind == "megagroup"


def test_load_legacy_state_defaults_source_kind_to_broadcast():
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    raw = json.loads(path.read_text())
    del raw["source_kind"]
    path.write_text(json.dumps(raw))

    assert state.load(s.clone_id).source_kind == "broadcast"


def test_state_accepts_basic_source_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="Legacy",
        source_kind="basic",
    )
    state.save(saved)
    assert state.load(saved.clone_id).source_kind == "basic"


def test_invalid_source_kind_is_policy_error():
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    raw = json.loads(path.read_text())
    raw["source_kind"] = "unknown"
    path.write_text(json.dumps(raw))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_state_round_trips_forum_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="Forum",
        source_kind="forum",
    )
    saved.record_topic(7, 1007)
    state.save(saved)
    loaded = state.load(saved.clone_id)
    assert loaded.source_kind == "forum"
    assert loaded.destination_kind == "forum"
    assert loaded.topic_dest_for(7) == 1007
    assert loaded.max_destination_id() == 1007


def test_state_defaults_forum_fields_for_legacy_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Old"
    )
    data = saved.to_dict()
    del data["destination_kind"], data["topic_map"]
    state.clones_dir().mkdir(parents=True)
    state.path_for(saved.clone_id).write_text(json.dumps(data))
    loaded = state.load(saved.clone_id)
    assert loaded.destination_kind == "broadcast"
    assert loaded.topic_map == {}


def test_state_rejects_unknown_destination_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Old"
    )
    data = saved.to_dict()
    data["destination_kind"] = "group"
    state.clones_dir().mkdir(parents=True)
    state.path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


def test_load_missing_returns_none():
    assert state.load(state.clone_id(1, 2)) is None


def test_save_writes_private_file():
    s = _fresh()
    state.save(s)
    mode = stat.S_IMODE(state.path_for(s.clone_id).stat().st_mode)
    assert mode == 0o600


def test_failed_atomic_replace_preserves_previous_state(monkeypatch):
    s = _fresh()
    s.cursor = 1
    state.save(s)
    original_replace = state.os.replace

    def fail_replace(*args):
        raise OSError("interrupted replace")

    monkeypatch.setattr(state.os, "replace", fail_replace)
    s.cursor = 2
    with pytest.raises(OSError, match="interrupted replace"):
        state.save(s)
    monkeypatch.setattr(state.os, "replace", original_replace)

    assert state.load(s.clone_id).cursor == 1


def test_unknown_version_is_policy_error():
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    raw = json.loads(path.read_text())
    raw["version"] = state.VERSION + 1
    path.write_text(json.dumps(raw))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_corrupted_file_is_policy_error():
    s = _fresh()
    state.save(s)
    state.path_for(s.clone_id).write_text("{ this is not json")

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_incomplete_state_is_policy_error():
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    raw = json.loads(path.read_text())
    del raw["source_title"]
    path.write_text(json.dumps(raw))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_cooldown_round_trip():
    s = _fresh()
    deadline = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    s.set_cooldown(deadline)
    state.save(s)

    loaded = state.load(s.clone_id)
    assert loaded.cooldown_deadline() == deadline


def test_set_cooldown_requires_timezone_aware():
    s = _fresh()
    with pytest.raises(ValueError):
        s.set_cooldown(datetime(2026, 7, 15, 12, 0))


def test_record_mapping_and_dest_for():
    s = _fresh()
    s.record_mapping(12, 5)
    assert s.dest_for(12) == 5
    assert s.dest_for(99) is None
    assert s.max_destination_id() == 5
