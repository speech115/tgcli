import sqlite3

import pytest

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
    assert reopened == authorized


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
