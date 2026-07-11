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


# --- Task 3: verdict and transport-fidelity comparison ---

from tgcli.mirror_lab import compare_transport, lab_verdict  # noqa: E402


def capability(kind, *, shas=(), bytes_state="pass", decode="pass"):
    return {
        "kind": kind,
        "sample_count": max(len(shas), 1),
        "coverage": "complete",
        "telethon_bytes": bytes_state,
        "samples": [
            {"kind": kind, "decode": decode, "telethon_bytes": bytes_state,
             "sha256": sha, "bytes": 1 if sha else None, "error": None}
            for sha in (shas or (None,))
        ],
    }


def report(*capabilities):
    return {"capabilities": list(capabilities)}


def seeded_manifest():
    manifest = new_manifest(7)
    record_seed(manifest, "protected_source", "photo", [1])
    record_seed(manifest, "protected_source", "poll", [2])
    record_seed(manifest, "protected_source", "album", [3, 4])
    return manifest


def test_lab_verdict_green_when_all_seeded_kinds_pass():
    result = lab_verdict(
        report(
            capability("photo", shas=("a",)),
            capability("poll", bytes_state="not_applicable"),
        ),
        seeded_manifest(),
        "protected_source",
    )
    assert result["verdict"] == "green"
    assert result["missing"] == [] and result["failing"] == []
    assert "giveaway" in result["excluded"]


def test_lab_verdict_red_on_missing_or_failing_kind():
    missing = lab_verdict(
        report(capability("photo", shas=("a",))),
        seeded_manifest(),
        "protected_source",
    )
    assert missing["verdict"] == "red" and missing["missing"] == ["poll"]

    failing = lab_verdict(
        report(
            capability("photo", shas=(), bytes_state="fail"),
            capability("poll", bytes_state="not_applicable"),
        ),
        seeded_manifest(),
        "protected_source",
    )
    assert failing["verdict"] == "red" and failing["failing"] == ["photo"]


def test_compare_transport_native_requires_exact_hashes():
    source = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_ok = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_bad = report(capability("video", shas=("zz",)), capability("photo", shas=("p1",)))

    ok = compare_transport(source, dest_ok, transport="native")
    assert ok["verdict"] == "green"
    assert {row["result"] for row in ok["rows"]} == {"pass"}

    bad = compare_transport(source, dest_bad, transport="native")
    assert bad["verdict"] == "red"
    video_row = next(r for r in bad["rows"] if r["kind"] == "video")
    assert video_row["result"] == "fail" and video_row["expectation"] == "exact"


def test_compare_transport_reupload_allows_photo_reencode():
    source = report(capability("photo", shas=("p1",)), capability("video", shas=("v1",)))
    dest = report(capability("photo", shas=("different",)), capability("video", shas=("v1",)))
    result = compare_transport(source, dest, transport="reupload")
    assert result["verdict"] == "green"
    photo_row = next(r for r in result["rows"] if r["kind"] == "photo")
    assert photo_row["expectation"] == "reencoded" and photo_row["result"] == "pass"


def test_compare_transport_flags_missing_dest_kind():
    source = report(capability("video", shas=("v1",)))
    result = compare_transport(source, report(), transport="native")
    assert result["verdict"] == "red"
    assert result["rows"][0]["result"] == "missing"
