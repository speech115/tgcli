"""SQLite/WAL clone-state backend (ADR-0060)."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

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


def test_connect_applies_wal_pragmas_and_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = tmp_path / "clones" / "x.db"
    path.parent.mkdir(parents=True)
    conn = statedb.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == statedb.SCHEMA_VERSION
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert tables >= {
            "meta",
            "id_map",
            "discussion_id_map",
            "topic_map",
            "avatar_photo_ids",
        }
    finally:
        conn.close()


def test_unknown_user_version_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = tmp_path / "clones" / "bad.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version={statedb.SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(PolicyError, match="unsupported schema version"):
        statedb.connect(path)


def test_save_then_load_round_trip_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.destination_peer_id = 1987654321
    s.cursor = 42
    s.record_mapping(7, 3)
    s.comments = "enabled"
    s.discussion_source_peer_id = 55
    s.discussion_destination_peer_id = 66
    s.discussion_linked = True
    s.discussion_cursor = 9
    s.record_discussion_mapping(3, 4)
    s.record_avatar(123, 999)
    state.save(s)

    assert state.path_for(s.clone_id).suffix == ".db"
    assert state.path_for(s.clone_id).is_file()
    assert not state.json_path_for(s.clone_id).exists()

    loaded = state.load(s.clone_id)
    assert loaded is not None
    assert loaded.to_dict() == s.to_dict()
    mode = stat.S_IMODE(state.path_for(s.clone_id).stat().st_mode)
    assert mode == 0o600


def test_save_one_mapping_is_o1_statements(tmp_path, monkeypatch):
    """Dirty-tracking: one new mapping must not rewrite the whole id_map."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    for i in range(1, 201):
        s.record_mapping(i, i)
        s.cursor = i
    state.save(s)

    loaded = state.load(s.clone_id)
    assert loaded is not None
    loaded.record_mapping(201, 201)
    loaded.cursor = 201

    path = state.path_for(loaded.clone_id)
    conn = sqlite3.connect(path)
    statements: list[str] = []

    def tracer(statement: str) -> None:
        if statement.startswith("--"):
            return
        statements.append(statement)

    conn.set_trace_callback(tracer)
    # Re-open through the public save path while counting via a patched connect.
    conn.close()

    real_connect = statedb.connect
    counted: list[str] = []

    def counting_connect(db_path: Path):
        c = real_connect(db_path)
        c.set_trace_callback(
            lambda statement: (
                counted.append(statement) if not statement.startswith("--") else None
            )
        )
        return c

    monkeypatch.setattr(statedb, "connect", counting_connect)
    state.save(loaded)

    # One transaction: meta upsert + one id_map insert — never 200 map writes.
    map_writes = [
        s
        for s in counted
        if "id_map" in s.lower()
        and s.lstrip().upper().startswith(("INSERT", "REPLACE", "UPDATE"))
    ]
    assert len(map_writes) <= 2
    assert len(counted) < 40


def test_load_rejects_seeded_invalid_meta(tmp_path, monkeypatch):
    """Fail-closed validation still runs after a SQLite read (ADR-0060)."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET source_kind = 'unknown' WHERE id = 1")
    conn.commit()
    conn.close()

    with pytest.raises(PolicyError, match="invalid"):
        state.load(s.clone_id)


def test_mapping_and_cursor_share_one_transaction(tmp_path, monkeypatch):
    """A crash mid-save must not leave a mapping without its cursor move."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    s.cursor = 10
    s.record_mapping(1, 100)
    state.save(s)

    loaded = state.load(s.clone_id)
    assert loaded is not None
    loaded.record_mapping(2, 200)
    loaded.cursor = 20

    real_connect = statedb.connect

    class BoomConn:
        def __init__(self, conn: sqlite3.Connection):
            self._conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self._conn.rollback()
                raise OSError("simulated crash before commit")
            self._conn.rollback()
            return False

        def close(self):
            self._conn.close()

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    monkeypatch.setattr(statedb, "connect", lambda path: BoomConn(real_connect(path)))
    with pytest.raises(OSError, match="simulated crash"):
        state.save(loaded)
    monkeypatch.setattr(statedb, "connect", real_connect)

    recovered = state.load(s.clone_id)
    assert recovered is not None
    assert recovered.cursor == 10
    assert recovered.dest_for(1) == 100
    assert recovered.dest_for(2) is None


def test_supersede_archives_db_and_wal_sidecars(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    # Force WAL sidecars to exist.
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO id_map(source, dest) VALUES (9, 90)")
    conn.commit()
    conn.close()
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    # Sidecars may or may not exist depending on checkpoint; touch if missing
    # so supersede's contract is exercised either way.
    if not wal.exists():
        wal.write_bytes(b"")
    if not shm.exists():
        shm.write_bytes(b"")

    archived = state.supersede(s.clone_id)

    assert not path.exists()
    assert not wal.exists()
    assert not shm.exists()
    names = sorted(p.name for p in archived)
    assert any(n.startswith(f"{s.clone_id}.db.superseded-") for n in names)
    assert any(n.startswith(f"{s.clone_id}.db-wal.superseded-") for n in names)
    assert any(n.startswith(f"{s.clone_id}.db-shm.superseded-") for n in names)


@given(
    pairs=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=10_000),
            st.integers(min_value=1, max_value=2_147_483_647),
        ),
        min_size=0,
        max_size=30,
        unique_by=lambda pair: pair[0],
    ).filter(lambda pairs: len({d for _, d in pairs}) == len(pairs))
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_record_save_reload_equals_memory(pairs, tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("clone-state")
    monkeypatch.setenv("TGCLI_STATE_DIR", str(root))
    s = state.CloneState.new(
        account_user_id=1,
        source_peer_id=abs(hash(tuple(pairs))) % 1_000_000_000 + 1,
        source_title="H",
    )
    for source, dest in pairs:
        s.record_mapping(source, dest)
        s.cursor = source
    expected = s.to_dict()
    state.save(s)
    loaded = state.load(s.clone_id)
    assert loaded is not None
    assert loaded.to_dict() == expected


def test_failed_integrity_check_is_policy_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    path = state.path_for(s.clone_id)
    # Truncate past the header so SQLite reports corruption.
    path.write_bytes(path.read_bytes()[:40])

    with pytest.raises(PolicyError, match="corrupted|integrity"):
        state.load(s.clone_id)


def _duplicate_dest_setup(table: str, s: state.CloneState) -> None:
    """Extra fields ``from_dict`` requires before a table's map may be
    non-empty (discussion_id_map needs an enabled discussion leg)."""
    if table == "discussion_id_map":
        s.comments = "enabled"
        s.discussion_source_peer_id = 55


_RECORD_MAPPING = {
    "id_map": state.CloneState.record_mapping,
    "discussion_id_map": state.CloneState.record_discussion_mapping,
    "avatar_photo_ids": state.CloneState.record_avatar,
}

_DEST_FOR = {
    "id_map": state.CloneState.dest_for,
    "discussion_id_map": state.CloneState.discussion_dest_for,
    "avatar_photo_ids": state.CloneState.avatar_for,
}


@pytest.mark.parametrize("table", ["id_map", "discussion_id_map", "avatar_photo_ids"])
def test_duplicate_destination_in_dirty_save_is_policy_error(
    table, tmp_path, monkeypatch
):
    """ADR-0060 UNIQUE(dest) guard: ``INSERT OR REPLACE`` resolves a
    UNIQUE(dest) collision by silently deleting the row that already held
    that destination — recording dest=100 for a new source when source=1
    already owns dest=100 must fail loudly (PolicyError) and roll back,
    never delete source=1's mapping without a trace. Exercised through the
    dirty-save path (the second save+), which is where the bug lived; the
    first (full) write already raises via a plain UNIQUE violation."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    record = _RECORD_MAPPING[table]
    dest_for = _DEST_FOR[table]

    s = _fresh()
    _duplicate_dest_setup(table, s)
    record(s, 1, 100)
    state.save(s)

    loaded = state.load(s.clone_id)
    assert loaded is not None
    record(loaded, 2, 100)  # new source, duplicate destination

    with pytest.raises(PolicyError, match="duplicate"):
        state.save(loaded)

    recovered = state.load(s.clone_id)
    assert recovered is not None
    assert dest_for(recovered, 1) == 100
    assert dest_for(recovered, 2) is None


def test_connect_restricts_wal_and_shm_sidecars_on_first_create(tmp_path, monkeypatch):
    """Schema creation (the first ``connect()`` for a clone) writes and
    commits before ``restrict_file`` ever touches the .db — so under a
    permissive umask, SQLite creates -wal/-shm at 0644 and only the .db
    itself gets chmod'd to 0600 afterwards, leaking the sidecars."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = tmp_path / "clones" / "x.db"
    path.parent.mkdir(parents=True)
    old_umask = os.umask(0o022)
    try:
        conn = statedb.connect(path)
    finally:
        os.umask(old_umask)
    try:
        wal, shm = statedb.sidecar_paths(path)
        assert wal.exists(), "expected a -wal sidecar right after schema creation"
        assert stat.S_IMODE(wal.stat().st_mode) == 0o600
        if shm.exists():
            assert stat.S_IMODE(shm.stat().st_mode) == 0o600
    finally:
        conn.close()


def test_persist_restricts_wal_and_shm_sidecars_before_close(tmp_path, monkeypatch):
    """Integration-level check through the public ``state.save`` seam: a
    save's sidecars must be 0600 while they exist, not just after SQLite's
    close-time auto-checkpoint may have already deleted them (so checking
    post-close is not a reliable test) — snapshot permissions right before
    the connection closes, the last moment they are guaranteed present."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    real_connect = statedb.connect
    captured: dict[str, int] = {}

    class SnapshotConn:
        def __init__(self, conn: sqlite3.Connection, db_path: Path):
            self._conn = conn
            self._db_path = db_path

        def close(self) -> None:
            wal, shm = statedb.sidecar_paths(self._db_path)
            for name, sidecar in (("wal", wal), ("shm", shm)):
                if sidecar.exists():
                    captured[name] = stat.S_IMODE(sidecar.stat().st_mode)
            self._conn.close()

        def __enter__(self):
            return self._conn.__enter__()

        def __exit__(self, *exc_info):
            return self._conn.__exit__(*exc_info)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    monkeypatch.setattr(statedb, "connect", lambda p: SnapshotConn(real_connect(p), p))

    old_umask = os.umask(0o022)
    try:
        s = _fresh()
        s.record_mapping(1, 100)
        state.save(s)
    finally:
        os.umask(old_umask)

    assert captured, "expected at least one sidecar to exist before connection close"
    assert all(mode == 0o600 for mode in captured.values()), captured


def test_probe_paths_both_files_present_reports_unreadable_shape(tmp_path, monkeypatch):
    """CONTRACT §11: a slot with both .db and .json is ambiguous and must
    report the documented unreadable shape (schema_version null, integrity
    naming the ambiguity) — not the real .db diagnostics, which hides the
    fact that manual resolution is required."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    json_path = state.json_path_for(s.clone_id)
    json_path.write_text("{}")

    probe = state.probe(s.clone_id)
    assert probe["schema_version"] is None
    assert probe["integrity"] != "ok"
    assert "both" in probe["integrity"] or "ambiguous" in probe["integrity"]
