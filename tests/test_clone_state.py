import json
import os
import stat
from datetime import UTC, datetime

import pytest

from tgcli.clone import roster, state, statedb
from tgcli.errors import PolicyError


def _valid_payload(**overrides):
    payload = {
        "version": state.VERSION,
        "account_user_id": 1,
        "source_peer_id": 2,
        "source_title": "S",
        "source_kind": "broadcast",
        "destination_kind": "broadcast",
        "topic_map": {},
    }
    payload.update(overrides)
    return payload


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
    assert s.clone_id == state.clone_id(100000001, 1234567890, "broadcast")


def test_clone_id_differs_by_peer_class_for_the_same_numeric_id():
    """ADR-0103: a User, a basic group, and a Channel can share one integer
    id; the clone identity must not collide just because the numeric peer
    id matches."""
    ids = {state.clone_id(1, 2, kind) for kind in ("dialog", "basic", "broadcast")}
    assert len(ids) == 3


def test_clone_id_is_stable_across_a_channel_kind_toggle():
    """megagroup <-> forum is an in-place Telegram toggle of one channel_id,
    not a different source; the slot must stay the same one so the existing
    `source_kind` drift check (not a new clone_id) is what catches it."""
    assert state.clone_id(1, 2, "megagroup") == state.clone_id(1, 2, "forum")
    assert state.clone_id(1, 2, "megagroup") == state.clone_id(1, 2, "broadcast")


def test_clone_id_still_differs_by_account_and_peer_for_a_fixed_kind():
    assert state.clone_id(1, 2, "broadcast") != state.clone_id(1, 3, "broadcast")
    assert state.clone_id(1, 2, "broadcast") != state.clone_id(9, 2, "broadcast")


def test_resolve_slot_returns_canonical_id_with_no_slot_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    assert state.resolve_slot(1, 2, "broadcast") == state.clone_id(1, 2, "broadcast")


def test_resolve_slot_migrates_a_pre_adr_0100_slot_in_place(tmp_path, monkeypatch):
    """A slot saved under the old kind-blind hash is found and moved onto
    its new kind-aware id the first time that identity is resolved again."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    legacy_id = state._kind_blind_clone_id(1, 2)
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Legacy", source_kind="basic"
    )
    saved.record_mapping(7, 70)
    state.statedb.persist(state.path_for(legacy_id), saved.to_dict(), full=True)

    canonical_id = state.resolve_slot(1, 2, "basic")

    assert canonical_id == state.clone_id(1, 2, "basic")
    assert canonical_id != legacy_id
    assert not state.path_for(legacy_id).exists()
    migrated = state.load(canonical_id)
    assert migrated is not None
    assert migrated.source_title == "Legacy"
    assert migrated.dest_for(7) == 70
    # Resolving again is a no-op: nothing left at the legacy id to migrate.
    assert state.resolve_slot(1, 2, "basic") == canonical_id


def test_resolve_slot_migrates_across_a_channel_kind_toggle(tmp_path, monkeypatch):
    """A legacy megagroup slot is still found when asked for as its forum
    toggle: same peer class, same channel_id, same slot."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    legacy_id = state._kind_blind_clone_id(1, 2)
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="Team chat",
        source_kind="megagroup",
    )
    state.statedb.persist(state.path_for(legacy_id), saved.to_dict(), full=True)

    canonical_id = state.resolve_slot(1, 2, "forum")

    assert canonical_id == state.clone_id(1, 2, "forum")
    assert not state.path_for(legacy_id).exists()
    assert state.load(canonical_id) is not None


def test_resolve_slot_ignores_a_legacy_slot_of_a_different_kind(tmp_path, monkeypatch):
    """A numeric-id collision across kinds (the bug ADR-0103 fixes) must not
    make one kind's resolution silently adopt the other kind's slot."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    legacy_id = state._kind_blind_clone_id(1, 2)
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="A dialog",
        source_kind="dialog",
    )
    state.statedb.persist(state.path_for(legacy_id), saved.to_dict(), full=True)

    canonical_id = state.resolve_slot(1, 2, "broadcast")

    assert canonical_id == state.clone_id(1, 2, "broadcast")
    assert state.load(canonical_id) is None
    # The dialog's own legacy slot is untouched and still resolvable by it.
    assert state.path_for(legacy_id).exists()


def test_resolve_slot_leaves_a_corrupt_legacy_slot_for_manual_repair(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    legacy_id = state._kind_blind_clone_id(1, 2)
    state.clones_dir().mkdir(parents=True)
    state.path_for(legacy_id).write_bytes(b"not a sqlite database")

    canonical_id = state.resolve_slot(1, 2, "broadcast")

    assert canonical_id == state.clone_id(1, 2, "broadcast")
    assert state.load(canonical_id) is None
    assert state.path_for(legacy_id).exists()


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


def test_load_legacy_state_defaults_source_kind_to_broadcast(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    del data["source_kind"]
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

    assert state.load(s.clone_id).source_kind == "broadcast"


def test_load_resumes_import_crashed_after_persist_before_rename(tmp_path, monkeypatch):
    """T06: a crash between the SQLite write and the final JSON rename must
    not leave the clone stuck behind the both-files "manual resolution
    required" PolicyError; the next load resumes and completes on its own.
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    state.clones_dir().mkdir(parents=True)
    json_path = state.json_path_for(s.clone_id)
    json_path.write_text(json.dumps(data))

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash before final rename")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="simulated crash"):
        state.load(s.clone_id)

    importing_path = json_path.with_name(json_path.name + ".importing")
    assert state.path_for(s.clone_id).exists(), "db must be fully written already"
    assert importing_path.exists()
    assert not json_path.exists()

    monkeypatch.setattr(os, "replace", real_replace)
    loaded = state.load(s.clone_id)
    assert loaded is not None
    assert loaded.source_title == s.source_title
    assert not importing_path.exists()
    imported_path = json_path.with_name(json_path.name + ".imported")
    assert imported_path.exists()


def test_load_resumes_import_crashed_before_persist(tmp_path, monkeypatch):
    """T06: a crash right after the JSON is renamed to ``.importing`` but
    before the SQLite write lands must also self-heal on the next load,
    not just the later crash window covered above."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    state.clones_dir().mkdir(parents=True)
    json_path = state.json_path_for(s.clone_id)
    json_path.write_text(json.dumps(data))

    real_persist = statedb.persist

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash during persist")

    monkeypatch.setattr(statedb, "persist", boom)
    with pytest.raises(OSError, match="simulated crash"):
        state.load(s.clone_id)

    importing_path = json_path.with_name(json_path.name + ".importing")
    assert importing_path.exists()
    assert not json_path.exists()
    assert not state.path_for(s.clone_id).exists()

    monkeypatch.setattr(statedb, "persist", real_persist)
    loaded = state.load(s.clone_id)
    assert loaded is not None
    assert loaded.source_title == s.source_title
    assert not importing_path.exists()
    assert state.path_for(s.clone_id).exists()


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


def test_invalid_source_kind_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    data["source_kind"] = "unknown"
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

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
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
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
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


def test_state_rejects_non_dict_topic_map(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Old"
    )
    data = saved.to_dict()
    data["topic_map"] = [["7", 1007]]
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


@pytest.mark.parametrize("value", [True, "1007"])
def test_state_rejects_non_int_topic_destination(tmp_path, monkeypatch, value):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    data = saved.to_dict()
    data["topic_map"] = {"7": value}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


@pytest.mark.parametrize("value", [0, -1])
def test_state_rejects_nonpositive_topic_destination(tmp_path, monkeypatch, value):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    data = saved.to_dict()
    data["topic_map"] = {"7": value}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


@pytest.mark.parametrize("key", ["", "topic", "01", "0", "-1", "١", "１"])
def test_state_rejects_noncanonical_topic_key(tmp_path, monkeypatch, key):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    data = saved.to_dict()
    data["topic_map"] = {key: 1007}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


@pytest.mark.parametrize(
    ("source_kind", "destination_kind", "topic_map"),
    [
        ("forum", "broadcast", {}),
        ("broadcast", "forum", {}),
        ("broadcast", "broadcast", {"7": 1007}),
    ],
)
def test_state_rejects_inconsistent_forum_fields(
    tmp_path, monkeypatch, source_kind, destination_kind, topic_map
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="Source",
        source_kind=source_kind,
    )
    data = saved.to_dict()
    data["destination_kind"] = destination_kind
    data["topic_map"] = topic_map
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


@pytest.mark.parametrize(
    ("source_topic_id", "destination_topic_id"),
    [("2147483648", 1), ("1", 2147483648)],
)
def test_state_rejects_topic_ids_above_tl_int_range(
    tmp_path, monkeypatch, source_topic_id, destination_topic_id
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    data = saved.to_dict()
    data["topic_map"] = {source_topic_id: destination_topic_id}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)


def test_state_accepts_max_tl_int_topic_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    maximum = 2_147_483_647
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    data = saved.to_dict()
    data["topic_map"] = {str(maximum): maximum}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(saved.clone_id).write_text(json.dumps(data))
    loaded = state.load(saved.clone_id)
    assert loaded.topic_dest_for(maximum) == maximum


@pytest.mark.parametrize(
    "topic_map",
    [
        {"1": 1001},
        {"2": 1},
        {"2": 1001, "3": 1001},
    ],
)
def test_state_rejects_non_bijective_non_general_topic_map(topic_map):
    data = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    ).to_dict()
    data["topic_map"] = topic_map

    with pytest.raises(ValueError, match="invalid topic map"):
        state.CloneState.from_dict(data)


@pytest.mark.parametrize(
    ("source_topic_id", "destination_topic_id"),
    [(1, 1001), (2, 1), (True, 1001), (2, True), (2_147_483_648, 1001)],
)
def test_record_topic_rejects_non_general_or_non_tl_int_ids(
    source_topic_id, destination_topic_id
):
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )

    with pytest.raises(ValueError, match="invalid topic mapping"):
        saved.record_topic(source_topic_id, destination_topic_id)

    assert saved.topic_map == {}


def test_record_topic_rejects_duplicate_destination():
    saved = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )
    saved.record_topic(2, 1001)

    with pytest.raises(ValueError, match="invalid topic mapping"):
        saved.record_topic(3, 1001)

    assert saved.topic_map == {"2": 1001}


def test_state_rejects_non_string_topic_key():
    data = _fresh().to_dict()
    data["topic_map"] = {7: 1007}
    with pytest.raises(ValueError, match="invalid topic map"):
        state.CloneState.from_dict(data)


def test_load_missing_returns_none():
    assert state.load(state.clone_id(1, 2, "broadcast")) is None


def test_save_writes_private_file():
    s = _fresh()
    state.save(s)
    mode = stat.S_IMODE(state.path_for(s.clone_id).stat().st_mode)
    assert mode == 0o600


def test_failed_save_preserves_previous_state(tmp_path, monkeypatch):
    """A crash during the save transaction must not tear mapping/cursor."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    from tgcli.clone import statedb

    s = _fresh()
    s.cursor = 1
    state.save(s)

    real_connect = statedb.connect

    class BoomConn:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self._conn.rollback()
                raise OSError("interrupted save")
            self._conn.rollback()
            return False

        def close(self):
            self._conn.close()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    s.cursor = 2
    monkeypatch.setattr(statedb, "connect", lambda path: BoomConn(real_connect(path)))
    with pytest.raises(OSError, match="interrupted save"):
        state.save(s)
    monkeypatch.setattr(statedb, "connect", real_connect)

    assert state.load(s.clone_id).cursor == 1


def test_unknown_version_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    data["version"] = state.VERSION + 1
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_corrupted_file_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    state.path_for(s.clone_id).write_bytes(b"not a sqlite database")

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


@pytest.mark.parametrize("payload", [[], "x", 42, None, True])
def test_non_dict_state_payload_is_policy_error(payload, tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = state.json_path_for("e" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))

    with pytest.raises(PolicyError):
        state.load("e" * 64)


def test_incomplete_state_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    del data["source_title"]
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

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


def test_set_cooldown_keeps_later_deadline():
    s = _fresh()
    longer = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
    shorter = datetime(2026, 7, 15, 12, 5, tzinfo=UTC)
    s.set_cooldown(longer)
    s.set_cooldown(shorter)
    assert s.cooldown_deadline() == longer


def test_record_mapping_and_dest_for():
    s = _fresh()
    s.record_mapping(12, 5)
    s.record_topic(13, 9)
    assert s.dest_for(12) == 5
    assert s.dest_for(99) is None
    assert s.max_destination_id() == 9


def test_new_state_defaults_to_no_comments():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    assert clone_state.comments == "none"
    assert clone_state.discussion_id_map == {}
    assert clone_state.discussion_cursor == 0
    assert clone_state.discussion_linked is False


def test_discussion_mapping_roundtrips():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    clone_state.discussion_destination_peer_id = 66
    clone_state.discussion_linked = True
    clone_state.discussion_cursor = 9
    clone_state.record_discussion_mapping(3, 4)
    state.save(clone_state)
    loaded = state.load(clone_state.clone_id)
    assert loaded.discussion_dest_for(3) == 4
    assert loaded.discussion_cursor == 9
    assert loaded.comments == "enabled"


def test_disabled_comments_roundtrips():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.comments = "disabled"
    state.save(clone_state)
    loaded = state.load(clone_state.clone_id)
    assert loaded.comments == "disabled"
    assert loaded.discussion_id_map == {}
    assert loaded.discussion_cursor == 0


def test_max_destination_id_excludes_discussion_ids():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.record_mapping(1, 10)
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    clone_state.record_discussion_mapping(1, 900)
    assert clone_state.max_destination_id() == 10
    assert clone_state.max_discussion_destination_id() == 900


def test_load_rejects_version_1_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = state.json_path_for("a" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "account_user_id": 1,
                "source_peer_id": 2,
                "source_title": "S",
            }
        )
    )
    with pytest.raises(PolicyError, match="unsupported version"):
        state.load("a" * 64)


def test_from_dict_rejects_unknown_comments_value():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(comments="maybe"))


def test_from_dict_rejects_enabled_comments_without_discussion_source():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(comments="enabled"))


def test_from_dict_rejects_discussion_map_without_enabled_comments():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(discussion_id_map={"1": 2}))


def test_from_dict_rejects_duplicate_discussion_destinations():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(
            _valid_payload(
                comments="enabled",
                discussion_source_peer_id=55,
                discussion_id_map={"1": 2, "3": 2},
            )
        )


@pytest.mark.parametrize("value", [True, "2", None, 2.0])
def test_from_dict_rejects_non_int_id_map_destination(value):
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(id_map={"1": value}))


@pytest.mark.parametrize("value", [0, -1, 2_147_483_648])
def test_from_dict_rejects_out_of_range_id_map_destination(value):
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(id_map={"1": value}))


@pytest.mark.parametrize("key", ["", "one", "01", "0", "-1", "１"])
def test_from_dict_rejects_noncanonical_id_map_key(key):
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(id_map={key: 5}))


def test_from_dict_rejects_duplicate_id_map_destinations():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(id_map={"1": 2, "3": 2}))


def test_from_dict_rejects_non_dict_id_map():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(id_map=[["1", 2]]))


@pytest.mark.parametrize(
    "value", ["soon", "2026-07-15T12:00:00", 42, "2026-07-15T12:00:00+25:00"]
)
def test_from_dict_rejects_invalid_retry_not_before(value):
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(retry_not_before=value))


def test_load_converts_invalid_id_map_to_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    data["id_map"] = {"1": "2"}
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_load_converts_naive_retry_not_before_to_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    data = s.to_dict()
    data["retry_not_before"] = "2026-07-15T12:00:00"
    state.clones_dir().mkdir(parents=True)
    state.json_path_for(s.clone_id).write_text(json.dumps(data))

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_supersede_missing_slot_is_noop():
    assert state.supersede("b" * 64) == []
    assert not state.path_for("b" * 64).exists()


def test_supersede_archives_state_and_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    cid = s.clone_id
    sidecar = roster.path_for(cid)
    sidecar.write_text('{"peer":"source"}\n')

    archived = state.supersede(cid, (sidecar,))

    assert not state.path_for(cid).exists()
    assert not sidecar.exists()
    names = sorted(p.name for p in archived)
    assert any(n.startswith(f"{cid}.db.superseded-") for n in names)
    assert any(n.startswith(f"{cid}-participants.jsonl.superseded-") for n in names)
    assert all(".superseded-" in n for n in names)


def test_supersede_state_only_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    archived = state.supersede(s.clone_id, (roster.path_for(s.clone_id),))
    assert any(p.name.startswith(f"{s.clone_id}.db.superseded-") for p in archived)
    assert not state.path_for(s.clone_id).exists()


def test_supersede_preserves_unreadable_v1_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cid = "c" * 64
    path = state.json_path_for(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "account_user_id": 1,
                "source_peer_id": 2,
                "source_title": "S",
            }
        )
    )
    archived = state.supersede(cid)
    assert len(archived) == 1
    assert not path.exists()
    assert json.loads(archived[0].read_text())["version"] == 1


def test_cooldown_deadline_clamps_a_clock_skewed_arm():
    """A deadline armed while the host clock ran ahead must not brick this
    clone slot forever — same ceiling the account-scoped record uses."""
    from datetime import timedelta

    from tgcli.clone import state as state_mod

    s = _fresh()
    s.retry_not_before = (datetime.now(UTC) + timedelta(days=400)).isoformat()

    deadline = s.cooldown_deadline()

    assert deadline is not None
    ceiling = datetime.now(UTC) + timedelta(seconds=state_mod.MAX_COOLDOWN_S + 5)
    assert deadline <= ceiling


def test_save_writes_sqlite_database(tmp_path, monkeypatch):
    """ADR-0060: active state is a mode-0600 SQLite database, not JSON."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.record_mapping(7, 70)
    state.save(s)

    path = state.path_for(s.clone_id)
    assert path.suffix == ".db"
    assert path.is_file()
    assert not state.json_path_for(s.clone_id).exists()
    assert state.load(s.clone_id).dest_for(7) == 70
