import pytest

from tgcli.errors import PolicyError
from tgcli.mirror_lab import (
    CHANNEL_ROLES,
    LAB_MARKER,
    assert_lab_peer,
    lab_peer_ids,
    load_manifest,
    new_manifest,
    record_channel,
    record_seed,
    save_manifest,
    seeded_ids,
)


def test_new_manifest_shape():
    manifest = new_manifest(account_user_id=42)
    assert manifest["manifest_version"] == 1
    assert manifest["account_user_id"] == 42
    assert manifest["channels"] == {}
    assert manifest["seeded"] == {}
    assert manifest["created_at"]


def test_manifest_round_trip(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, f"{LAB_MARKER} open_source x")
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_load_manifest_rejects_unknown_version(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text('{"manifest_version": 99}')
    with pytest.raises(ValueError):
        load_manifest(path)


def test_record_channel_requires_known_role_and_marker():
    manifest = new_manifest(7)
    with pytest.raises(ValueError):
        record_channel(manifest, "mystery", 100, f"{LAB_MARKER} x")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, "innocent channel")


def test_assert_lab_peer_blocks_foreign_peers():
    manifest = new_manifest(7)
    record_channel(manifest, "dest_native", 200, f"{LAB_MARKER} dest_native x")
    assert lab_peer_ids(manifest) == {200}
    assert_lab_peer(manifest, 200)
    with pytest.raises(PolicyError):
        assert_lab_peer(manifest, 999)


def test_channel_roles_are_frozen():
    assert CHANNEL_ROLES == (
        "protected_source",
        "open_source",
        "dest_native",
        "dest_reupload",
    )


def test_record_and_read_seeds():
    manifest = new_manifest(7)
    record_seed(manifest, "open_source", "photo", [11])
    record_seed(manifest, "open_source", "album", [12, 13])
    assert seeded_ids(manifest, "open_source") == {
        "album": [12, 13],
        "photo": [11],
    }
    assert seeded_ids(manifest, "protected_source") == {}
