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


# --- Task 2: fixture matrix and payload generators ---

from telethon.tl import types  # noqa: E402

from tgcli.mirror_probe import NON_BYTE_KINDS, classify_message  # noqa: E402
from tgcli.mirror_lab import (  # noqa: E402
    BYTE_FIXTURES,
    COVERED_BY_R0,
    EXCLUDED_KINDS,
    NON_BYTE_LAB_KINDS,
    build_png,
    deterministic_bytes,
    non_byte_media,
    pending_kinds,
    planned_kinds,
)


def test_png_payload_is_valid_signature_and_deterministic():
    payload = build_png((255, 0, 0))
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload == build_png((255, 0, 0))
    assert payload != build_png((0, 255, 0))


def test_deterministic_bytes_are_stable_and_sized():
    blob = deterministic_bytes("video", 1_600_000)
    assert len(blob) == 1_600_000
    assert blob == deterministic_bytes("video", 1_600_000)
    assert blob[:64] != deterministic_bytes("audio", 64)


def test_byte_fixture_matrix_covers_every_byte_kind():
    assert sorted(BYTE_FIXTURES) == [
        "animation", "audio", "document", "photo",
        "sticker", "video", "video_note", "voice",
    ]
    for kind, fixture in BYTE_FIXTURES.items():
        assert fixture.kind == kind
        assert fixture.payload()  # non-empty bytes
        assert isinstance(fixture.attributes(), list)
        assert fixture.reupload_fidelity in {"exact", "reencoded"}
    assert BYTE_FIXTURES["photo"].reupload_fidelity == "reencoded"
    assert BYTE_FIXTURES["document"].force_document is True


def test_video_fixture_crosses_download_chunk_boundary():
    assert len(BYTE_FIXTURES["video"].payload()) > 512 * 1024


def test_non_byte_media_constructs_pinned_layer_objects():
    media = non_byte_media()
    assert sorted(media) == sorted(NON_BYTE_LAB_KINDS)
    assert isinstance(media["poll"], types.InputMediaPoll)
    assert isinstance(media["todo"], types.InputMediaTodo)
    assert isinstance(media["contact"], types.InputMediaContact)
    assert isinstance(media["geo"], types.InputMediaGeoPoint)
    assert isinstance(media["geo_live"], types.InputMediaGeoLive)
    assert isinstance(media["venue"], types.InputMediaVenue)
    assert isinstance(media["dice"], types.InputMediaDice)


def test_every_probe_kind_is_planned_excluded_or_covered():
    all_kinds = set(NON_BYTE_KINDS) | set(BYTE_FIXTURES)
    planned = set(planned_kinds())
    accounted = planned | set(EXCLUDED_KINDS) | COVERED_BY_R0
    assert all_kinds <= accounted
    assert not (set(EXCLUDED_KINDS) & planned)


def test_fixture_attributes_classify_back_to_expected_kind():
    from types import SimpleNamespace as NS

    for kind in ("voice", "video_note", "animation", "sticker", "audio", "video"):
        fixture = BYTE_FIXTURES[kind]
        document = NS(
            mime_type="application/octet-stream",
            attributes=[
                a for a in fixture.attributes()
                if not isinstance(a, types.DocumentAttributeFilename)
            ],
        )
        message = NS(
            id=1, action=None, message="",
            media=types.MessageMediaDocument(document=document),
        )
        assert classify_message(message) == kind


def test_pending_kinds_shrink_as_seeds_are_recorded():
    manifest = new_manifest(7)
    assert pending_kinds(manifest, "open_source") == planned_kinds()
    record_seed(manifest, "open_source", "photo", [11])
    assert "photo" not in pending_kinds(manifest, "open_source")
    assert "photo" in pending_kinds(manifest, "protected_source")
