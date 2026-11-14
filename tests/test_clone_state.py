import os
import sqlite3
import stat

import pytest

from tests.fakes.clone import corrupt_slot, save_under
from tgcli.clone import roster, state, statedb
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
    save_under(saved, legacy_id)

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
    save_under(saved, legacy_id)

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
    save_under(saved, legacy_id)

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


def _forum() -> state.CloneState:
    return state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="Forum", source_kind="forum"
    )


def _assert_load_fails(saved: state.CloneState, *statements: str) -> None:
    corrupt_slot(saved, *statements)
    with pytest.raises(PolicyError, match="invalid"):
        state.load(saved.clone_id)


def test_invalid_source_kind_is_policy_error():
    _assert_load_fails(_fresh(), "UPDATE meta SET source_kind = 'unknown'")


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


def test_state_rejects_unknown_destination_kind():
    _assert_load_fails(_fresh(), "UPDATE meta SET destination_kind = 'group'")


@pytest.mark.parametrize(
    ("source_kind", "destination_kind", "topic_rows"),
    [
        ("forum", "broadcast", ()),
        ("broadcast", "forum", ()),
        ("broadcast", "broadcast", ((7, 1007),)),
    ],
)
def test_state_rejects_inconsistent_forum_fields(
    source_kind, destination_kind, topic_rows
):
    saved = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="Source",
        source_kind=source_kind,
    )
    _assert_load_fails(
        saved,
        f"UPDATE meta SET destination_kind = '{destination_kind}'",
        *(f"INSERT INTO topic_map VALUES ({s}, {d})" for s, d in topic_rows),
    )


@pytest.mark.parametrize(
    ("source_topic_id", "destination_topic_id"),
    [
        (1, 1001),
        (2, 1),
        (2, 0),
        (2, -1),
        (2_147_483_648, 1001),
        (7, 2_147_483_648),
    ],
)
def test_state_rejects_topic_ids_outside_the_tl_range(
    source_topic_id, destination_topic_id
):
    _assert_load_fails(
        _forum(),
        f"INSERT INTO topic_map VALUES ({source_topic_id}, {destination_topic_id})",
    )


def test_state_accepts_max_tl_int_topic_ids():
    maximum = 2_147_483_647
    saved = _forum()
    saved.record_topic(maximum, maximum)
    state.save(saved)

    assert state.load(saved.clone_id).topic_dest_for(maximum) == maximum


@pytest.mark.parametrize(
    ("source_topic_id", "destination_topic_id"),
    [(1, 1001), (2, 1), (True, 1001), (2, True), (2_147_483_648, 1001)],
)
def test_record_topic_rejects_non_general_or_non_tl_int_ids(
    source_topic_id, destination_topic_id
):
    saved = _forum()

    with pytest.raises(ValueError, match="invalid topic mapping"):
        saved.record_topic(source_topic_id, destination_topic_id)

    assert saved.topic_map == {}


def test_record_topic_rejects_duplicate_destination():
    saved = _forum()
    saved.record_topic(2, 1001)

    with pytest.raises(ValueError, match="invalid topic mapping"):
        saved.record_topic(3, 1001)

    assert saved.topic_map == {2: 1001}


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
    monkeypatch.setattr(
        statedb, "connect", lambda path, **kw: BoomConn(real_connect(path, **kw))
    )
    with pytest.raises(OSError, match="interrupted save"):
        state.save(s)
    monkeypatch.setattr(statedb, "connect", real_connect)

    assert state.load(s.clone_id).cursor == 1


def test_corrupted_file_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    state.path_for(s.clone_id).write_bytes(b"not a sqlite database")

    with pytest.raises(PolicyError):
        state.load(s.clone_id)


def test_a_database_without_its_meta_row_is_policy_error():
    saved = _fresh()
    corrupt_slot(saved, "DELETE FROM meta")

    with pytest.raises(PolicyError, match="no meta row"):
        state.load(saved.clone_id)


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


def test_unknown_comments_value_fails_to_load():
    _assert_load_fails(_fresh(), "UPDATE meta SET comments = 'maybe'")


def test_enabled_comments_without_a_discussion_source_fail_to_load():
    saved = _fresh()
    saved.comments = state.Comments.ENABLED
    _assert_load_fails(saved)


def test_a_discussion_map_without_enabled_comments_fails_to_load():
    saved = _fresh()
    saved.record_discussion_mapping(1, 2)
    _assert_load_fails(saved)


def _paused_discussion(**overrides) -> state.CloneState:
    """A linked clone whose source unlinked its group, with comment progress."""
    saved = _fresh()
    saved.discussion_linked = True
    saved.discussion_source_peer_id = 55
    saved.discussion_destination_peer_id = 888
    saved.discussion_cursor = 12
    saved.record_discussion_mapping(12, 5)
    for name, value in overrides.items():
        setattr(saved, name, value)
    return saved


def test_comment_progress_on_a_paused_linked_clone_loads():
    saved = _paused_discussion()
    state.save(saved)

    loaded = state.load(saved.clone_id)

    assert loaded.comments == "none"
    assert loaded.discussion_cursor == 12
    assert loaded.discussion_dest_for(12) == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"discussion_source_peer_id": None},
        {"discussion_linked": False, "discussion_destination_peer_id": None},
        {"source_kind": "megagroup"},
    ],
    ids=["no-discussion-source", "unlinked", "non-broadcast"],
)
def test_comment_progress_that_cannot_resume_fails_to_load(overrides):
    _assert_load_fails(_paused_discussion(**overrides))


@pytest.mark.parametrize("table", ["id_map", "anchor_map"])
@pytest.mark.parametrize(
    ("source", "dest"),
    [(1, 0), (1, -1), (1, 2_147_483_648), (0, 5), (-1, 5), (2_147_483_648, 5)],
)
def test_map_ids_outside_the_tl_range_fail_to_load(table, source, dest):
    _assert_load_fails(_fresh(), f"INSERT INTO {table} VALUES ({source}, {dest})")


def test_discussion_ids_outside_the_tl_range_fail_to_load():
    saved = _fresh()
    saved.comments = state.Comments.ENABLED
    saved.discussion_source_peer_id = 55
    _assert_load_fails(saved, "INSERT INTO discussion_id_map VALUES (1, 0)")


def test_a_negative_discussion_cursor_fails_to_load():
    saved = _fresh()
    saved.comments = state.Comments.ENABLED
    saved.discussion_source_peer_id = 55
    _assert_load_fails(saved, "UPDATE meta SET discussion_cursor = -1")


@pytest.mark.parametrize(
    "value",
    ["'soon'", "'2026-07-15T12:00:00'", "42", "'2026-07-15T12:00:00+25:00'"],
)
def test_an_invalid_or_naive_retry_not_before_fails_to_load(value):
    _assert_load_fails(_fresh(), f"UPDATE meta SET retry_not_before = {value}")


def test_a_duplicate_destination_cannot_be_stored_at_all():
    """The schema refuses it, so load never has to check."""
    saved = _fresh()
    saved.record_mapping(1, 2)
    state.save(saved)
    conn = sqlite3.connect(state.path_for(saved.clone_id))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO id_map VALUES (3, 2)")
    finally:
        conn.close()


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


def test_save_writes_sqlite_database(tmp_path, monkeypatch):
    """ADR-0060: active state is a mode-0600 SQLite database, not JSON."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.record_mapping(7, 70)
    state.save(s)

    path = state.path_for(s.clone_id)
    assert path.suffix == ".db"
    assert path.is_file()
    assert state.load(s.clone_id).dest_for(7) == 70


@pytest.fixture
def usual_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_clone_state_and_roster_dirs_are_private_under_umask_022(usual_umask):
    """A fresh state root must not come out 0755 from a clone write."""
    import asyncio
    from types import SimpleNamespace

    from tgcli.state_paths import state_dir

    assert not state_dir().exists()
    s = _fresh()
    state.save(s)

    assert _mode(state_dir()) == 0o700
    assert _mode(state.clones_dir()) == 0o700

    state.clones_dir().chmod(0o755)

    class NoPeople:
        async def iter_participants(self, entity, limit=None):
            return
            yield

    asyncio.run(roster.collect(NoPeople(), s, SimpleNamespace(id=1)))

    assert _mode(state.clones_dir()) == 0o700


def test_clone_media_cache_dir_is_private_under_umask_022(usual_umask):
    from tgcli.clone import reupload

    s = _fresh()
    cache = reupload.ensure_cache_dir(s)

    assert cache == reupload.cache_dir(s)
    assert _mode(cache) == 0o700
    assert _mode(state.clones_dir()) == 0o700


def test_a_remembered_flood_is_left_until_its_wait_is_over(monkeypatch):
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(state, "_now", lambda: start)
    saved = _fresh()
    assert saved.flood_left() is None

    saved.remember_flood(600)

    assert saved.retry_not_before == "2026-11-14T12:10:00+00:00"
    assert saved.flood_left() == 600
    monkeypatch.setattr(state, "_now", lambda: start + timedelta(seconds=599.2))
    assert saved.flood_left() == 1
    monkeypatch.setattr(state, "_now", lambda: start + timedelta(seconds=600))
    assert saved.flood_left() is None


def test_a_remembered_flood_survives_save_and_load():
    saved = _fresh()
    saved.remember_flood(90)
    state.save(saved)

    loaded = state.load(saved.clone_id)

    assert loaded.retry_not_before == saved.retry_not_before
    assert 0 < loaded.flood_left() <= 90
