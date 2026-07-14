import fcntl
import json
import sqlite3
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from tgcli.mirror import store as mirror_store
from tgcli.mirror.store import MirrorStore, mirror_id


def test_mirror_identity_is_stable_and_state_is_peer_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))

    assert mirror_id(42, -100123) == mirror_id(42, -100123)
    assert mirror_id(42, -100123) != mirror_id(43, -100123)
    assert mirror_id(42, -100123) != mirror_id(42, -100124)

    first = MirrorStore()
    first_record = first.create(42, -100123, "Source channel")
    second = MirrorStore()
    second_record = second.create(42, -100124, "Other channel")

    assert first.path == tmp_path / "mirrors" / f"{first_record.mirror_id}.db"
    assert second.path == tmp_path / "mirrors" / f"{second_record.mirror_id}.db"
    assert first.path != second.path


def test_create_reopens_metadata_and_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))

    store = MirrorStore()
    created = store.create(42, -100123, "Source channel")
    assert created.account_user_id == 42
    assert created.source_peer_id == -100123
    assert created.source_title == "Source channel"
    assert created.destination_peer_id is None
    assert created.authorized is False

    authorized = store.authorize(-100999)
    assert authorized.destination_peer_id == -100999
    assert authorized.authorized is True

    reopened = MirrorStore().create(42, -100123, "Changed title")
    assert reopened.source_title == "Changed title"
    assert reopened.destination_peer_id == authorized.destination_peer_id
    assert reopened.authorized is True


def test_existing_database_migrates_without_losing_mirror_or_copy_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    identity = mirror_id(42, -100123)
    mirrors_dir = tmp_path / "mirrors"
    mirrors_dir.mkdir()
    database = mirrors_dir / f"{identity}.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE mirrors (
                mirror_id TEXT PRIMARY KEY,
                account_user_id INTEGER NOT NULL,
                source_peer_id INTEGER NOT NULL,
                source_title TEXT NOT NULL,
                destination_peer_id INTEGER,
                authorized INTEGER NOT NULL DEFAULT 0,
                high_water_message_id INTEGER NOT NULL DEFAULT 0,
                UNIQUE (account_user_id, source_peer_id)
            );
            CREATE TABLE copy_operations (
                source_peer_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                random_id INTEGER NOT NULL,
                destination_message_id INTEGER,
                PRIMARY KEY (source_peer_id, source_message_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO mirrors VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (identity, 42, -100123, "Source", -100999, 1, 7),
        )
        connection.execute(
            """
            INSERT INTO copy_operations VALUES (?, ?, ?, ?)
            """,
            (-100123, 7, -77, 700),
        )

    store = MirrorStore()
    migrated = store.create(42, -100123, "Source")

    assert migrated.destination_peer_id == -100999
    assert migrated.authorized is True
    assert migrated.creation_marker is None
    assert migrated.creation_state == "authorized"
    assert migrated.create_attempted_at is None
    assert migrated.retention_class == "user_owned_retained"
    assert store.prepare_copy(7, random_id=999).random_id == -77
    assert store.prepare_copy(7).destination_message_id == 700
    assert store.last_confirmed_message_id() == 7


def test_mark_create_dispatched_persists_marker_state_and_utc_time(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    attempted_at = datetime(2026, 7, 14, 12, 30, tzinfo=timezone(timedelta(hours=4)))

    dispatched = store.mark_create_dispatched("tgcli-mirror-abcd", attempted_at)
    reopened = MirrorStore().create(42, -100123, "Source")

    assert dispatched.creation_marker == "tgcli-mirror-abcd"
    assert dispatched.creation_state == "reconcile_required"
    assert dispatched.create_attempted_at == "2026-07-14T08:30:00+00:00"
    assert dispatched.retention_class == "provisional"
    assert reopened == dispatched


def test_authorize_atomically_records_retained_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    store.mark_create_dispatched(
        "tgcli-mirror-abcd", datetime(2026, 7, 14, tzinfo=timezone.utc)
    )

    authorized = store.authorize(-100999)

    assert authorized.destination_peer_id == -100999
    assert authorized.authorized is True
    assert authorized.creation_state == "authorized"
    assert authorized.retention_class == "user_owned_retained"


def test_authorize_rolls_back_all_fields_when_retention_update_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    store.mark_create_dispatched(
        "tgcli-mirror-abcd", datetime(2026, 7, 14, tzinfo=timezone.utc)
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_retention
            BEFORE UPDATE OF retention_class ON mirrors
            BEGIN
                SELECT RAISE(ABORT, 'forced retention failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced retention failure"):
        store.authorize(-100999)

    current = MirrorStore().create(42, -100123, "Source")
    assert current.destination_peer_id is None
    assert current.authorized is False
    assert current.creation_state == "reconcile_required"
    assert current.retention_class == "provisional"


def test_mark_create_blocked_is_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    dispatched = store.mark_create_dispatched(
        "tgcli-mirror-abcd", datetime(2026, 7, 14, tzinfo=timezone.utc)
    )

    blocked = store.mark_create_blocked()
    reopened = MirrorStore().create(42, -100123, "Source")

    assert blocked.creation_state == "blocked"
    assert blocked.creation_marker == dispatched.creation_marker
    assert blocked.create_attempted_at == dispatched.create_attempted_at
    assert reopened == blocked


def test_cooldown_is_account_scoped_private_utc_and_never_shortens(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    local_now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone(timedelta(hours=4)))

    first_deadline = mirror_store.record_cooldown(420001, 600, now=local_now)
    shorter_result = mirror_store.record_cooldown(420001, 30, now=local_now)
    second_deadline = mirror_store.record_cooldown(430002, 60, now=local_now)

    assert first_deadline == datetime(2026, 7, 14, 8, 10, tzinfo=timezone.utc)
    assert shorter_result == first_deadline
    assert mirror_store.cooldown_deadline(420001) == first_deadline
    assert mirror_store.cooldown_deadline(430002) == second_deadline
    assert mirror_store.cooldown_deadline(999999) is None

    cooldown_files = sorted((tmp_path / "mirrors" / "cooldowns").glob("*.json"))
    assert len(cooldown_files) == 2
    assert all("420001" not in path.name and "430002" not in path.name for path in cooldown_files)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in cooldown_files)
    assert not list((tmp_path / "mirrors" / "cooldowns").glob("*.tmp"))
    payloads = [json.loads(path.read_text()) for path in cooldown_files]
    assert {payload["retry_not_before"] for payload in payloads} == {
        "2026-07-14T08:01:00+00:00",
        "2026-07-14T08:10:00+00:00",
    }


def test_concurrent_cooldown_writers_retain_the_later_deadline(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    account_user_id = 420001
    now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
    start = threading.Barrier(3)
    original_read = mirror_store._read_cooldown

    def assert_read_holds_cooldown_lock(path):
        probe = mirror_store._open_private_lock(path.with_suffix(".lock"))
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return original_read(path)
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise AssertionError("cooldown read ran outside the write lock")
        finally:
            mirror_store.os.close(probe)

    monkeypatch.setattr(
        mirror_store, "_read_cooldown", assert_read_holds_cooldown_lock
    )
    results = {}
    errors = []

    def write(name, retry_after):
        try:
            start.wait(timeout=1)
            results[name] = mirror_store.record_cooldown(
                account_user_id, retry_after, now=now
            )
        except Exception as exc:
            errors.append(exc)

    long_writer = threading.Thread(
        target=write, args=("long", 600), name="long-writer"
    )
    short_writer = threading.Thread(
        target=write, args=("short", 30), name="short-writer"
    )
    long_writer.start()
    short_writer.start()
    start.wait(timeout=1)
    long_writer.join(timeout=3)
    short_writer.join(timeout=3)

    assert not long_writer.is_alive()
    assert not short_writer.is_alive()
    assert errors == []
    expected = now + timedelta(seconds=600)
    assert original_read(mirror_store._cooldown_path(account_user_id)) == expected
    assert results["long"] == expected
    assert results["short"] in {now + timedelta(seconds=30), expected}


def test_cooldown_replace_fsyncs_parent_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fsync_targets = []
    original_fsync = mirror_store.os.fsync

    def recording_fsync(fd):
        fsync_targets.append(stat.S_ISDIR(mirror_store.os.fstat(fd).st_mode))
        original_fsync(fd)

    monkeypatch.setattr(mirror_store.os, "fsync", recording_fsync)

    mirror_store.record_cooldown(
        420001,
        600,
        now=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
    )

    assert fsync_targets == [False, True]


def test_copy_operations_are_unique_per_source_peer_and_reuse_random_id(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    first = MirrorStore()
    first.create(42, -100123, "First")
    second = MirrorStore()
    second.create(42, -100124, "Second")

    first_operation = first.prepare_copy(7, random_id=-9)
    retried = first.prepare_copy(7, random_id=999)
    second_operation = second.prepare_copy(7, random_id=10)

    assert retried == first_operation
    assert first_operation.source_peer_id == -100123
    assert first_operation.source_message_id == 7
    assert first_operation.random_id == -9
    assert first_operation.destination_message_id is None
    assert second_operation.source_peer_id == -100124
    assert second_operation.random_id == 10
    assert first.pending_copies() == [first_operation]


def test_generated_random_id_is_persisted_signed_64_bit(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")

    operation = store.prepare_copy(8)
    reopened = MirrorStore()
    reopened.create(42, -100123, "Source")

    assert -(2**63) <= operation.random_id <= 2**63 - 1
    assert reopened.prepare_copy(8).random_id == operation.random_id


@pytest.mark.parametrize("random_id", [-(2**63), 2**63 - 1])
def test_prepare_copy_accepts_signed_64_bit_boundaries(
    tmp_path, monkeypatch, random_id
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")

    assert store.prepare_copy(8, random_id=random_id).random_id == random_id


@pytest.mark.parametrize(
    ("random_id", "error"),
    [
        (-(2**63) - 1, ValueError),
        (2**63, ValueError),
        (False, TypeError),
        (True, TypeError),
    ],
)
def test_prepare_copy_rejects_out_of_range_and_boolean_random_ids(
    tmp_path, monkeypatch, random_id, error
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")

    with pytest.raises(error):
        store.prepare_copy(8, random_id=random_id)


def test_confirm_copy_atomically_maps_message_and_advances_high_water(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    store.prepare_copy(7, random_id=70)
    store.prepare_copy(9, random_id=90)

    confirmed = store.confirm_copy(7, destination_message_id=700)

    assert confirmed.destination_message_id == 700
    assert store.last_confirmed_message_id() == 7
    assert store.pending_copies() == [store.prepare_copy(9)]

    store.confirm_copy(9, destination_message_id=900)
    assert store.last_confirmed_message_id() == 9
    assert store.pending_copies() == []


def test_confirm_copy_rolls_back_mapping_when_high_water_update_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    store = MirrorStore()
    store.create(42, -100123, "Source")
    pending = store.prepare_copy(7, random_id=70)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_high_water
            BEFORE UPDATE OF high_water_message_id ON mirrors
            BEGIN
                SELECT RAISE(ABORT, 'forced cursor failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced cursor failure"):
        store.confirm_copy(7, destination_message_id=700)

    assert store.pending_copies() == [pending]
    assert store.last_confirmed_message_id() == 0
