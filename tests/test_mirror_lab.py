import json
import hashlib
import os
import subprocess
import asyncio

import pytest

from tgcli import mirror_lab as lab_module

REAL_MATERIALIZE_FIXTURE = getattr(lab_module, "materialize_fixture", None)


@pytest.fixture(autouse=True)
def lightweight_fixture_materializer(monkeypatch):
    def materialize(kind, directory, *, color=(0, 0, 255)):
        fixture = lab_module.BYTE_FIXTURES[kind]
        path = Path(directory) / fixture.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_png(color) if kind == "photo" else fixture.payload()
        path.write_bytes(payload)
        return path

    monkeypatch.setattr(lab_module, "materialize_fixture", materialize)

from tgcli.errors import PolicyError
from tgcli.mirror_lab import (
    CHANNEL_ROLES,
    LAB_MARKER,
    assert_lab_peer,
    lab_title,
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
    assert manifest["manifest_version"] == 3
    assert len(manifest["lab_id"]) == 24
    assert manifest["account_user_id"] == 42
    assert manifest["channels"] == {}
    assert manifest["seeded"] == {}
    assert manifest["scenarios"] == {}
    assert manifest["created_at"]


def test_manifest_round_trip(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_valid_v2_manifest_migrates_in_memory_without_rewrite_or_evidence(tmp_path):
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy.pop("scenarios")
    legacy["seeded"] = {"open_source": {"photo": [11]}}
    legacy["blocked"] = {"open_source": {"poll": "blocked"}}
    path = tmp_path / "lab.json"
    save_manifest(path, legacy)
    before = path.read_bytes()

    loaded = load_manifest(path)

    assert loaded["manifest_version"] == 3
    assert loaded["scenarios"] == {}
    assert loaded["seeded"] == legacy["seeded"]
    assert loaded["blocked"] == legacy["blocked"]
    assert path.read_bytes() == before


def test_load_manifest_rejects_unknown_version(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text('{"manifest_version": 99}')
    with pytest.raises(ValueError):
        load_manifest(path)


def test_v2_manifest_rejects_scenario_payload(tmp_path):
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy["scenarios"] = {}
    path = tmp_path / "lab.json"
    save_manifest(path, legacy)

    with pytest.raises(ValueError, match="v2.*scenarios"):
        load_manifest(path)


def test_manifest_version_requires_an_exact_integer(tmp_path):
    manifest = new_manifest(7)
    manifest["manifest_version"] = 3.0
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="unsupported lab manifest"):
        load_manifest(path)


def test_compatibility_fingerprint_shape_and_defensive_role_copy():
    roles = {
        "operator": {"alias": "main", "user_id": 101},
        "lab_peer": {"alias": "lab-peer", "user_id": 202},
    }
    fingerprint = lab_module.new_compatibility_fingerprint(
        "basic.open",
        fixture_schema_version=4,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding=roles,
        config_digest="c" * 64,
    )
    assert fingerprint == {
        "fingerprint_version": 1,
        "scenario_key": "basic.open",
        "fixture_schema_version": 4,
        "lab_code_digest": "a" * 64,
        "mirror_code_digest": "b" * 64,
        "telethon_version": "1.44.0",
        "telegram_schema_layer": 216,
        "account_role_binding": {
            "operator": {"alias": "main", "user_id": 101},
            "lab_peer": {"alias": "lab-peer", "user_id": 202},
        },
        "config_digest": "c" * 64,
    }
    roles["operator"]["alias"] = "mutated"
    assert fingerprint["account_role_binding"]["operator"]["alias"] == "main"


def test_scenario_checkpoint_shape_defensive_copy_and_v3_round_trip(tmp_path):
    fingerprint = lab_module.new_compatibility_fingerprint(
        "channel.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )
    checkpoint = lab_module.new_scenario_checkpoint("channel.open", fingerprint)
    assert checkpoint == {
        "checkpoint_version": 1,
        "phase": "preflight",
        "created_peers": {},
        "created_topics": {},
        "outbound_operations": {},
        "verdicts": {},
        "cleanup_obligations": [],
        "compatibility_fingerprint": fingerprint,
    }
    fingerprint["account_role_binding"]["operator"]["alias"] = "mutated"
    assert checkpoint["compatibility_fingerprint"]["account_role_binding"]["operator"]["alias"] == "main"

    manifest = new_manifest(7)
    manifest["scenarios"]["channel.open"] = checkpoint
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_scenario_checkpoint_rejects_mismatched_fingerprint_key():
    fingerprint = lab_module.new_compatibility_fingerprint(
        "basic.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )

    with pytest.raises(ValueError, match="scenario key mismatch"):
        lab_module.new_scenario_checkpoint("basic.protected", fingerprint)

    assert fingerprint["scenario_key"] == "basic.open"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("checkpoint_version", 2, "checkpoint version"),
        ("checkpoint_version", True, "checkpoint version"),
        ("phase", "paused", "phase"),
    ),
)
def test_invalid_checkpoint_version_or_phase_rejects(tmp_path, field, value, message):
    fingerprint = lab_module.new_compatibility_fingerprint(
        "channel.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )
    manifest = new_manifest(7)
    checkpoint = lab_module.new_scenario_checkpoint("channel.open", fingerprint)
    checkpoint[field] = value
    manifest["scenarios"]["channel.open"] = checkpoint
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)

    with pytest.raises(ValueError, match=message):
        load_manifest(path)


def test_malformed_compatibility_fingerprints_reject(tmp_path):
    def valid_fingerprint():
        return lab_module.new_compatibility_fingerprint(
            "basic.open",
            fixture_schema_version=1,
            lab_code_digest="a" * 64,
            mirror_code_digest="b" * 64,
            telethon_version="1.44.0",
            telegram_schema_layer=216,
            account_role_binding={
                "operator": {"alias": "main", "user_id": 101},
                "lab_peer": {"alias": "lab-peer", "user_id": 202},
            },
            config_digest="c" * 64,
        )

    cases = (
        lambda fp: fp.update(fingerprint_version=2),
        lambda fp: fp.update(fingerprint_version=True),
        lambda fp: fp.update(scenario_key="basic.protected"),
        lambda fp: fp.update(fixture_schema_version=0),
        lambda fp: fp.update(fixture_schema_version=True),
        lambda fp: fp.update(lab_code_digest="A" * 64),
        lambda fp: fp.update(mirror_code_digest="b" * 63),
        lambda fp: fp.update(config_digest="z" * 64),
        lambda fp: fp.update(telethon_version=""),
        lambda fp: fp.update(telegram_schema_layer=0),
        lambda fp: fp.update(telegram_schema_layer=True),
        lambda fp: fp.update(account_role_binding={}),
        lambda fp: fp["account_role_binding"].update(
            observer={"alias": "other", "user_id": 303}
        ),
        lambda fp: fp["account_role_binding"]["operator"].update(alias=""),
        lambda fp: fp["account_role_binding"]["operator"].update(user_id=0),
        lambda fp: fp["account_role_binding"]["operator"].update(user_id=True),
        lambda fp: fp["account_role_binding"]["lab_peer"].update(alias=""),
        lambda fp: fp["account_role_binding"]["lab_peer"].update(user_id=101),
    )
    path = tmp_path / "lab.json"
    for mutate in cases:
        fingerprint = valid_fingerprint()
        manifest = new_manifest(7)
        manifest["scenarios"]["basic.open"] = lab_module.new_scenario_checkpoint(
            "basic.open", fingerprint
        )
        # Exercise embedded-key mismatch through saved data, not the builder.
        mutate(fingerprint)
        manifest["scenarios"]["basic.open"]["compatibility_fingerprint"] = fingerprint
        save_manifest(path, manifest)
        with pytest.raises(ValueError):
            load_manifest(path)


def test_checkpoint_fields_scenario_keys_and_container_types_are_strict(tmp_path):
    def valid_checkpoint(scenario_key="channel.open"):
        fingerprint = lab_module.new_compatibility_fingerprint(
            scenario_key,
            fixture_schema_version=1,
            lab_code_digest="a" * 64,
            mirror_code_digest="b" * 64,
            telethon_version="1.44.0",
            telegram_schema_layer=216,
            account_role_binding={"operator": {"alias": "main", "user_id": 101}},
            config_digest="c" * 64,
        )
        return lab_module.new_scenario_checkpoint(scenario_key, fingerprint)

    path = tmp_path / "lab.json"
    invalid_manifests = []

    missing_scenarios = new_manifest(7)
    missing_scenarios.pop("scenarios")
    invalid_manifests.append(missing_scenarios)
    wrong_scenarios = new_manifest(7)
    wrong_scenarios["scenarios"] = []
    invalid_manifests.append(wrong_scenarios)
    unknown_scenario = new_manifest(7)
    unknown_scenario["scenarios"]["unknown.open"] = valid_checkpoint("unknown.open")
    invalid_manifests.append(unknown_scenario)
    non_dict_checkpoint = new_manifest(7)
    non_dict_checkpoint["scenarios"]["channel.open"] = []
    invalid_manifests.append(non_dict_checkpoint)

    for field in (
        "checkpoint_version",
        "phase",
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
        "cleanup_obligations",
        "compatibility_fingerprint",
    ):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        checkpoint.pop(field)
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    unknown_field = new_manifest(7)
    checkpoint = valid_checkpoint()
    checkpoint["extra"] = {}
    unknown_field["scenarios"]["channel.open"] = checkpoint
    invalid_manifests.append(unknown_field)

    for field, wrong_value in (
        ("created_peers", []),
        ("created_topics", []),
        ("outbound_operations", []),
        ("verdicts", []),
        ("cleanup_obligations", {}),
    ):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        checkpoint[field] = wrong_value
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    for fingerprint_change in ("missing", "unknown"):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        fingerprint = checkpoint["compatibility_fingerprint"]
        if fingerprint_change == "missing":
            fingerprint.pop("config_digest")
        else:
            fingerprint["extra"] = "value"
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    for manifest in invalid_manifests:
        save_manifest(path, manifest)
        with pytest.raises(ValueError):
            load_manifest(path)


def test_v2_migration_rejects_invalid_legacy_provenance_and_creating(tmp_path):
    path = tmp_path / "lab.json"
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy.pop("scenarios")
    legacy["channels"] = {
        "open_source": {
            "peer_id": True,
            "title": lab_title(legacy, "open_source"),
        }
    }
    save_manifest(path, legacy)
    with pytest.raises(ValueError, match="peer id"):
        load_manifest(path)

    legacy["channels"] = {}
    legacy["creating"] = {
        "open_source": {
            "title": lab_title(legacy, "open_source"),
            "state": "invented",
        }
    }
    save_manifest(path, legacy)
    with pytest.raises(ValueError, match="creating state"):
        load_manifest(path)


def test_load_manifest_rejects_unmarked_or_duplicate_channel_entries(tmp_path):
    manifest = new_manifest(7)
    manifest["channels"] = {
        "open_source": {"peer_id": 100, "title": "not a lab"},
    }
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="provenance"):
        load_manifest(path)

    manifest["channels"] = {
        "open_source": {"peer_id": 100, "title": lab_title(manifest, "open_source")},
        "dest_native": {"peer_id": 100, "title": lab_title(manifest, "dest_native")},
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_load_manifest_rejects_stale_or_invalid_creating_entries(tmp_path):
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    manifest["creating"] = {
        "protected_source": {
            "title": f"{LAB_MARKER} stale-lab protected_source",
            "state": "ambiguous",
        }
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="creating title"):
        load_manifest(path)

    manifest["creating"]["protected_source"] = {
        "title": lab_title(manifest, "protected_source"),
        "state": "invented",
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="creating state"):
        load_manifest(path)


def test_record_channel_requires_known_role_and_marker():
    manifest = new_manifest(7)
    with pytest.raises(ValueError):
        record_channel(manifest, "mystery", 100, f"{LAB_MARKER} x")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, "innocent channel")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, f"{LAB_MARKER} forged")


def test_assert_lab_peer_blocks_foreign_peers():
    manifest = new_manifest(7)
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
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


def test_required_scenario_keys_cover_every_topology_protection_cell():
    assert hasattr(lab_module, "required_scenarios")
    assert tuple(spec.key for spec in lab_module.required_scenarios()) == (
        "basic.open",
        "basic.protected",
        "supergroup.open",
        "supergroup.protected",
        "forum.open",
        "forum.protected",
        "channel.open",
        "channel.protected",
        "channel_plain.open_open",
        "channel_plain.protected_open",
        "channel_plain.open_protected",
        "channel_plain.protected_protected",
        "channel_forum.open_open",
        "channel_forum.protected_open",
        "channel_forum.open_protected",
        "channel_forum.protected_protected",
    )


def test_full_content_suite_is_allocated_only_to_standalone_peer_families():
    scenarios = lab_module.required_scenarios()
    assert all(hasattr(spec, "content_profile") for spec in scenarios)
    assert {
        spec.key for spec in scenarios if spec.content_profile == "full"
    } == {
        "basic.open",
        "basic.protected",
        "supergroup.open",
        "supergroup.protected",
        "forum.open",
        "forum.protected",
        "channel.open",
        "channel.protected",
    }
    assert {
        spec.key for spec in scenarios if spec.content_profile == "sentinels"
    } == {
        "channel_plain.open_open",
        "channel_plain.protected_open",
        "channel_plain.open_protected",
        "channel_plain.protected_protected",
        "channel_forum.open_open",
        "channel_forum.protected_open",
        "channel_forum.open_protected",
        "channel_forum.protected_protected",
    }


def test_scenario_specs_encode_source_and_discussion_protection_independently():
    scenarios = lab_module.required_scenarios()
    fields = (
        "source_family",
        "source_protected",
        "discussion_kind",
        "discussion_protected",
    )
    assert all(hasattr(spec, field) for spec in scenarios for field in fields)
    assert {
        (
            spec.source_family,
            spec.source_protected,
            spec.discussion_kind,
            spec.discussion_protected,
        )
        for spec in scenarios
    } == {
        ("basic", False, "none", None),
        ("basic", True, "none", None),
        ("supergroup", False, "none", None),
        ("supergroup", True, "none", None),
        ("forum", False, "none", None),
        ("forum", True, "none", None),
        ("channel", False, "none", None),
        ("channel", True, "none", None),
        *{
            ("channel", channel_protected, discussion_kind, discussion_protected)
            for channel_protected in (False, True)
            for discussion_kind in ("plain", "forum")
            for discussion_protected in (False, True)
        },
    }


def test_basic_sources_are_normalized_to_supergroup_destinations():
    scenarios = lab_module.required_scenarios()
    assert all(hasattr(spec, "destination_family") for spec in scenarios)
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "basic"
    } == {"supergroup"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "supergroup"
    } == {"supergroup"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "forum"
    } == {"forum"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "channel"
    } == {"channel"}


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
from telethon.errors import ChannelPrivateError, FloodWaitError  # noqa: E402

from tgcli.mirror_probe import NON_BYTE_KINDS, classify_message  # noqa: E402
from tgcli.mirror_lab import (  # noqa: E402
    BYTE_FIXTURES,
    COVERED_BY_R0,
    EXCLUDED_KINDS,
    NON_BYTE_LAB_KINDS,
    ALBUM_COLORS,
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


@pytest.mark.skipif(
    os.environ.get("TGCLI_LAB_FIXTURE_SMOKE") != "1",
    reason="requires the lab-only ffmpeg/cwebp toolchain",
)
def test_materialized_media_fixtures_are_valid_and_deterministic(tmp_path):
    expected = {
        "photo": ("jpeg_pipe", "mjpeg"),
        "audio": ("mp3", "mp3"),
        "voice": ("ogg", "opus"),
        "video": ("mov,mp4,m4a,3gp,3g2,mj2", "h264"),
        "video_note": ("mov,mp4,m4a,3gp,3g2,mj2", "h264"),
        "animation": ("gif", "gif"),
        "sticker": ("webp_pipe", "webp"),
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for kind, (format_name, codec_name) in expected.items():
        path_a = REAL_MATERIALIZE_FIXTURE(kind, first)
        path_b = REAL_MATERIALIZE_FIXTURE(kind, second)
        assert hashlib.sha256(path_a.read_bytes()).digest() == hashlib.sha256(
            path_b.read_bytes()
        ).digest()
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=format_name:stream=codec_name", "-of", "json", str(path_a),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        metadata = json.loads(probe.stdout)
        assert metadata["format"]["format_name"] == format_name
        assert metadata["streams"][0]["codec_name"] == codec_name
        if kind == "video":
            assert {stream["codec_name"] for stream in metadata["streams"]} == {
                "h264",
                "aac",
            }
    assert (first / BYTE_FIXTURES["video"].filename).stat().st_size > 512 * 1024

    red = REAL_MATERIALIZE_FIXTURE("photo", first / "red", color=(255, 0, 0))
    green = REAL_MATERIALIZE_FIXTURE("photo", first / "green", color=(0, 255, 0))
    assert hashlib.sha256(red.read_bytes()).digest() != hashlib.sha256(
        green.read_bytes()
    ).digest()


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
    assert isinstance(media["contact"], types.InputMediaContact)
    assert isinstance(media["geo"], types.InputMediaGeoPoint)
    assert isinstance(media["venue"], types.InputMediaVenue)
    assert isinstance(media["dice"], types.InputMediaDice)


def test_every_probe_kind_is_planned_excluded_or_covered():
    all_kinds = set(NON_BYTE_KINDS) | set(BYTE_FIXTURES)
    planned = set(planned_kinds())
    accounted = planned | set(EXCLUDED_KINDS) | COVERED_BY_R0
    assert all_kinds <= accounted
    assert not (set(EXCLUDED_KINDS) & planned)


def test_todo_is_explicitly_unsupported_after_live_server_rejection():
    assert "todo" not in planned_kinds()
    assert "MediaInvalidError" in EXCLUDED_KINDS["todo"]


def test_geo_live_is_explicitly_unsupported_after_native_fidelity_failure():
    assert "geo_live" not in planned_kinds()
    assert "static geo" in EXCLUDED_KINDS["geo_live"]


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


def report(*capabilities, probe_version=2):
    return {"probe_version": probe_version, "capabilities": list(capabilities)}


def seeded_manifest():
    manifest = new_manifest(7)
    rows = []
    next_id = 1
    for kind in planned_kinds():
        ids = [next_id, next_id + 1] if kind == "album" else [next_id]
        next_id += len(ids)
        record_seed(manifest, "protected_source", kind, ids)
        if kind == "album":
            continue
        if kind in BYTE_FIXTURES:
            rows.append(capability(kind, shas=(f"{kind}-sha",)))
        else:
            rows.append(capability(kind, bytes_state="not_applicable"))
    return manifest, rows


def test_lab_verdict_green_when_all_seeded_kinds_pass():
    manifest, rows = seeded_manifest()
    result = lab_verdict(
        report(*rows),
        manifest,
        "protected_source",
    )
    assert result["verdict"] == "green"
    assert result["missing"] == [] and result["failing"] == []
    assert "giveaway" in result["excluded"]


def test_lab_verdict_red_on_missing_or_failing_kind():
    manifest, rows = seeded_manifest()
    missing = lab_verdict(
        report(*(row for row in rows if row["kind"] != "poll")),
        manifest,
        "protected_source",
    )
    assert missing["verdict"] == "red" and missing["missing"] == ["poll"]

    failing = lab_verdict(
        report(*(
            capability("photo", shas=(), bytes_state="fail")
            if row["kind"] == "photo" else row
            for row in rows
        )),
        manifest,
        "protected_source",
    )
    assert failing["verdict"] == "red" and failing["failing"] == ["photo"]


def test_lab_verdict_rejects_empty_or_partial_seed_matrix():
    empty = new_manifest(7)
    result = lab_verdict(report(), empty, "protected_source")
    assert result["verdict"] == "red"
    assert result["pending"]


def test_lab_verdict_rejects_blocked_or_old_schema_matrix():
    blocked = new_manifest(7)
    for kind in planned_kinds():
        lab_module.record_blocked(blocked, "protected_source", kind, "RuntimeError")
    result = lab_verdict(report(), blocked, "protected_source")
    assert result["verdict"] == "red"
    assert sorted(result["blocked"]) == sorted(planned_kinds())

    manifest, rows = seeded_manifest()
    result = lab_verdict(
        report(*rows, probe_version=1), manifest, "protected_source"
    )
    assert result["verdict"] == "red"
    assert result["schema"] == "unsupported"


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

    inconclusive = report(
        capability("video", shas=("v1",), bytes_state="inconclusive")
    )
    result = compare_transport(inconclusive, inconclusive, transport="native")
    assert result["verdict"] == "red"


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


def test_compare_transport_cannot_greenlight_incomplete_expected_matrix():
    collapsed = report(capability("document", shas=("same",)))
    result = compare_transport(
        collapsed,
        collapsed,
        transport="native",
        expected_kinds={"document", "audio", "video"},
    )
    assert result["verdict"] == "red"
    assert {row["kind"] for row in result["rows"] if row["result"] == "missing"} == {
        "audio",
        "video",
    }

    observed = report(capability("document", shas=("same",)))
    result = compare_transport(
        observed, observed, transport="native", expected_kinds=set()
    )
    assert result["verdict"] == "red"


def test_compare_transport_checks_non_byte_kinds_and_album_grouping():
    source = report(
        capability("text", bytes_state="not_applicable"),
        capability("poll", bytes_state="not_applicable"),
    )
    source["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]
    dest = report(
        capability("text", bytes_state="not_applicable"),
        capability("poll", bytes_state="not_applicable"),
    )
    dest["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]

    result = compare_transport(
        source,
        dest,
        transport="native",
        expected_kinds={"text", "poll", "album"},
    )
    assert result["verdict"] == "green"

    dest["album_groups"] = [
        {"count": 2, "sha256": ["red", "red"], "telethon_bytes": ["pass", "pass"]}
    ]
    result = compare_transport(
        source,
        dest,
        transport="native",
        expected_kinds={"text", "poll", "album"},
    )
    assert result["verdict"] == "red"
    assert next(row for row in result["rows"] if row["kind"] == "album")["result"] == "fail"


def test_compare_transport_rejects_old_schema_and_reupload_album_drift():
    source = report(capability("video", shas=("v1",)), probe_version=1)
    dest = report(capability("video", shas=("v1",)))
    result = compare_transport(source, dest, transport="native")
    assert result["verdict"] == "red"
    assert result["schema"] == "unsupported"

    source = report()
    source["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]
    dest = report()
    dest["album_groups"] = [
        {"count": 2, "sha256": ["green", "red"], "telethon_bytes": ["pass", "pass"]}
    ]
    result = compare_transport(
        source, dest, transport="reupload", expected_kinds={"album"}
    )
    assert result["verdict"] == "red"
    assert result["rows"] == [
        {"kind": "album", "expectation": "grouped", "result": "fail"}
    ]


@pytest.mark.asyncio
async def test_album_groups_report_ordered_constituent_hashes():
    class AlbumTG:
        async def iter_messages(self, entity, limit):
            for message_id, grouped_id in ((3, 10), (2, 10), (1, None)):
                yield NS(
                    id=message_id,
                    grouped_id=grouped_id,
                    media=types.MessageMediaPhoto(
                        photo=NS(payload=b"red" if message_id == 2 else b"green")
                    ),
                    action=None,
                    message="",
                )

        async def iter_download(self, media, request_size):
            yield media.photo.payload

    tg = AlbumTG()
    groups = await lab_module.album_groups(tg, NS(id=1), limit=20)
    assert groups[0]["count"] == 2
    assert len(groups[0]["sha256"]) == 2
    assert groups[0]["sha256"][0] != groups[0]["sha256"][1]


# --- Task 4: provisioning and seeding engines ---

from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402
from telethon.tl import functions  # noqa: E402

from tgcli.mirror_lab import create_lab_channels, seed_sources  # noqa: E402


class FakeTG:
    """Records raw requests and high-level sends; returns canned results."""

    def __init__(self):
        self.raw_requests = []
        self.sent_files = []
        self.sent_messages = []
        self._next_channel_id = 100
        self._next_message_id = 1000
        self._channels = {}

    async def __call__(self, request):
        self.raw_requests.append(request)
        if isinstance(request, functions.channels.CreateChannelRequest):
            self._next_channel_id += 1
            channel = NS(
                id=self._next_channel_id,
                title=request.title,
                creator=True,
                broadcast=True,
                megagroup=False,
                noforwards=False,
            )
            self._channels[channel.id] = channel
            return NS(chats=[channel])
        if isinstance(request, functions.messages.ToggleNoForwardsRequest):
            peer_id = getattr(request.peer, "id", getattr(request.peer, "channel_id", None))
            if peer_id in self._channels:
                self._channels[peer_id].noforwards = request.enabled
        if isinstance(request, functions.messages.SendMediaRequest):
            self._next_message_id += 1
            return NS(updates=[NS(message=NS(id=self._next_message_id))])
        return NS(updates=[])

    async def get_entity(self, ref):
        peer_id = getattr(ref, "channel_id", ref)
        return self._channels.get(peer_id, NS(id=peer_id, title=""))

    async def send_message(self, entity, text):
        self._next_message_id += 1
        self.sent_messages.append(text)
        return NS(id=self._next_message_id)

    async def send_file(self, entity, file, **kwargs):
        self.sent_files.append((entity, file, kwargs))
        if isinstance(file, list):
            out = []
            for _ in file:
                self._next_message_id += 1
                out.append(NS(id=self._next_message_id))
            return out
        self._next_message_id += 1
        return NS(id=self._next_message_id)


@pytest.mark.asyncio
async def test_byte_seed_uses_named_path_and_explicit_mime_type(tmp_path):
    tg = FakeTG()
    await lab_module._seed_kind(tg, NS(id=1), "audio", tmp_path)
    _, uploaded, kwargs = tg.sent_files[-1]
    assert isinstance(uploaded, Path)
    assert uploaded.name == "lab-audio.mp3"
    assert kwargs["mime_type"] == "audio/mpeg"


@pytest.mark.asyncio
async def test_album_seed_uses_named_photo_paths(tmp_path):
    tg = FakeTG()
    await lab_module._seed_kind(tg, NS(id=1), "album", tmp_path)
    _, uploaded, _ = tg.sent_files[-1]
    assert all(isinstance(path, Path) for path in uploaded)
    assert [path.suffix for path in uploaded] == [".jpg", ".jpg"]


def quiet(_message):
    pass


@pytest.mark.asyncio
async def test_create_lab_channels_records_all_roles_and_protects_source(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert set(manifest["channels"]) == set(CHANNEL_ROLES)
    for entry in manifest["channels"].values():
        assert entry["title"].startswith(LAB_MARKER)
    toggles = [
        r for r in tg.raw_requests
        if isinstance(r, functions.messages.ToggleNoForwardsRequest)
    ]
    assert len(toggles) == 1 and toggles[0].enabled is True
    assert load_manifest(path)["channels"] == manifest["channels"]


@pytest.mark.asyncio
async def test_create_lab_channels_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    created = len(tg.raw_requests)
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    assert len(tg.raw_requests) == created  # no second creation


@pytest.mark.asyncio
async def test_create_reconciles_ambiguous_accepted_channel_without_duplicate(tmp_path):
    class AcceptThenCancelCreateTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.cancel_once = True

        async def __call__(self, request):
            if (
                self.cancel_once
                and isinstance(request, functions.channels.CreateChannelRequest)
            ):
                self.cancel_once = False
                await super().__call__(request)
                raise asyncio.CancelledError()
            return await super().__call__(request)

        async def iter_dialogs(self):
            for entity in self._channels.values():
                yield NS(entity=entity)

    tg = AcceptThenCancelCreateTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"

    with pytest.raises(asyncio.CancelledError):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    persisted = load_manifest(path)
    assert persisted["channels"] == {}
    assert persisted["creating"]["protected_source"]["state"] == "ambiguous"

    await create_lab_channels(tg, persisted, path, "labacct", quiet)
    creates = [
        request for request in tg.raw_requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]
    assert len(creates) == len(CHANNEL_ROLES)
    assert set(load_manifest(path)["channels"]) == set(CHANNEL_ROLES)


@pytest.mark.asyncio
async def test_create_reconciles_disconnect_after_server_acceptance(tmp_path):
    class AcceptThenDisconnectTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.disconnect_once = True

        async def __call__(self, request):
            if (
                self.disconnect_once
                and isinstance(request, functions.channels.CreateChannelRequest)
            ):
                self.disconnect_once = False
                await super().__call__(request)
                raise ConnectionError("connection lost after dispatch")
            return await super().__call__(request)

        async def iter_dialogs(self):
            for entity in self._channels.values():
                yield NS(entity=entity)

    tg = AcceptThenDisconnectTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"

    with pytest.raises(ConnectionError, match="after dispatch"):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    persisted = load_manifest(path)
    assert persisted["creating"]["protected_source"]["state"] == "ambiguous"

    await create_lab_channels(tg, persisted, path, "labacct", quiet)
    creates = [
        request for request in tg.raw_requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]
    assert len(creates) == len(CHANNEL_ROLES)


@pytest.mark.asyncio
async def test_create_retries_protected_toggle_after_partial_failure(tmp_path):
    class ToggleFailsOnceTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.toggle_attempts = 0

        async def __call__(self, request):
            if isinstance(request, functions.messages.ToggleNoForwardsRequest):
                self.raw_requests.append(request)
                self.toggle_attempts += 1
                if self.toggle_attempts == 1:
                    raise RuntimeError("temporary toggle failure")
            return await super().__call__(request)

    tg = ToggleFailsOnceTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    with pytest.raises(RuntimeError, match="toggle failure"):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    created_before_retry = sum(
        isinstance(request, functions.channels.CreateChannelRequest)
        for request in tg.raw_requests
    )

    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert tg.toggle_attempts == 2
    assert sum(
        isinstance(request, functions.channels.CreateChannelRequest)
        for request in tg.raw_requests
    ) == created_before_retry + 3


@pytest.mark.asyncio
async def test_create_repairs_live_protection_even_if_manifest_claims_complete(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    protected_id = manifest["channels"]["protected_source"]["peer_id"]
    tg._channels[protected_id].noforwards = False
    toggles_before = sum(
        isinstance(request, functions.messages.ToggleNoForwardsRequest)
        for request in tg.raw_requests
    )

    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert tg._channels[protected_id].noforwards is True
    assert sum(
        isinstance(request, functions.messages.ToggleNoForwardsRequest)
        for request in tg.raw_requests
    ) == toggles_before + 1


@pytest.mark.asyncio
async def test_seed_sources_covers_plan_and_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)

    for role in ("protected_source", "open_source"):
        assert sorted(seeded_ids(manifest, role)) == sorted(planned_kinds())
        assert set(results[role].values()) == {"seeded"}
        assert len(seeded_ids(manifest, role)["album"]) == len(ALBUM_COLORS)

    sent_before = len(tg.sent_files)
    again = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert len(tg.sent_files) == sent_before
    assert again == {"protected_source": {}, "open_source": {}}


@pytest.mark.asyncio
async def test_seed_sources_records_server_rejection_explicitly(tmp_path):
    class RejectingTG(FakeTG):
        async def send_file(self, entity, file, **kwargs):
            attributes = kwargs.get("attributes") or []
            if any(type(a).__name__ == "DocumentAttributeSticker" for a in attributes):
                raise RuntimeError("STICKER_INVALID")
            return await super().send_file(entity, file, **kwargs)

    tg = RejectingTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert results["open_source"]["sticker"] == "blocked:RuntimeError"
    assert "sticker" not in seeded_ids(manifest, "open_source")


@pytest.mark.asyncio
async def test_seed_stops_immediately_on_flood_wait(tmp_path):
    class FloodTG(FakeTG):
        async def send_message(self, entity, text):
            raise FloodWaitError(request=None, capture=42)

    tg = FloodTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    with pytest.raises(FloodWaitError):
        await seed_sources(tg, manifest, path, "labacct", quiet)

    assert seeded_ids(manifest, "protected_source") == {}
    assert seeded_ids(manifest, "open_source") == {}


@pytest.mark.asyncio
async def test_seed_refuses_manifest_peer_without_live_lab_ownership(tmp_path):
    class ForeignTG(FakeTG):
        def __init__(self, title):
            super().__init__()
            self.title = title

        async def get_entity(self, ref):
            return NS(
                id=getattr(ref, "channel_id", ref),
                title=self.title,
                creator=False,
                broadcast=True,
                megagroup=False,
            )

    manifest = new_manifest(7)
    record_channel(manifest, "protected_source", 100, lab_title(manifest, "protected_source"))
    record_channel(manifest, "open_source", 200, lab_title(manifest, "open_source"))

    with pytest.raises(PolicyError, match="owned lab broadcast"):
        await seed_sources(
            ForeignTG(lab_title(manifest, "open_source")),
            manifest,
            tmp_path / "lab.json",
            "labacct",
            quiet,
        )


@pytest.mark.asyncio
async def test_mutations_respect_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_READONLY", "1")
    tg = FakeTG()
    manifest = new_manifest(7)
    with pytest.raises(PolicyError):
        await create_lab_channels(tg, manifest, tmp_path / "lab.json", "labacct", quiet)
    assert tg.raw_requests == []


@pytest.mark.asyncio
async def test_create_writes_correlated_attempt_and_result_audit(tmp_path):
    from tgcli.safety import audit_path

    tg = FakeTG()
    await create_lab_channels(tg, new_manifest(7), tmp_path / "lab.json", "labacct", quiet)

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    by_operation = {}
    for record in records:
        by_operation.setdefault(record["operation_id"], []).append(record)
    assert len(by_operation) == 5  # four creates plus protected toggle
    for pair in by_operation.values():
        assert [record["phase"] for record in pair] == ["attempt", "result"]
        assert pair[-1]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_cancelled_mutation_records_ambiguous_audit_result():
    from tgcli.safety import audit_path

    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await lab_module._audited_mutation("lab-test", "labacct", {}, cancel)

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    assert [record["phase"] for record in records] == ["attempt", "result"]
    assert records[-1]["status"] == "ambiguous"


# --- Task 5: copy transports ---

from pathlib import Path  # noqa: E402

from telethon.errors import ChatForwardsRestrictedError  # noqa: E402

from tgcli.mirror_lab import copy_native, copy_reupload  # noqa: E402


class TransportTG(FakeTG):
    def __init__(self):
        super().__init__()
        self.forwards = []
        self.downloads = []

    async def __call__(self, request):
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            from_protected = getattr(self, "protected_peer_id", None) == getattr(
                request.from_peer, "id", request.from_peer
            )
            if from_protected:
                raise ChatForwardsRestrictedError(request=request)
            self.forwards.append(request)
            return NS(updates=[])
        return await super().__call__(request)

    async def get_messages(self, entity, ids):
        return [
            NS(id=i, document=NS(attributes=[]), media=object()) for i in ids
        ]

    async def download_media(self, message, file):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload-%d" % message.id)
        self.downloads.append(path)
        return str(path)


async def seeded_lab(tg, tmp_path):
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    await seed_sources(tg, manifest, path, "labacct", quiet)
    tg.protected_peer_id = manifest["channels"]["protected_source"]["peer_id"]
    return manifest


@pytest.mark.asyncio
async def test_copy_native_confirms_restriction_and_forwards_per_kind(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    result = await copy_native(tg, manifest, "labacct", quiet)

    assert result["transport"] == "native"
    assert result["restricted_check"] == "confirmed"
    assert set(result["results"]) == set(planned_kinds())
    assert all(v == "forwarded" for v in result["results"].values())
    for request in tg.forwards:
        assert request.drop_author is True
        assert len(request.random_id) == len(request.id)


@pytest.mark.asyncio
async def test_copy_native_records_per_kind_blocks(tmp_path):
    class GeoBlockingTG(TransportTG):
        async def __call__(self, request):
            if isinstance(request, functions.messages.ForwardMessagesRequest):
                if getattr(self, "geo_ids", None) and set(request.id) & self.geo_ids:
                    raise RuntimeError("MEDIA_INVALID")
            return await super().__call__(request)

    tg = GeoBlockingTG()
    manifest = await seeded_lab(tg, tmp_path)
    tg.geo_ids = set(seeded_ids(manifest, "open_source")["geo"])
    result = await copy_native(tg, manifest, "labacct", quiet)
    assert result["results"]["geo"] == "blocked:RuntimeError"
    assert result["results"]["photo"] == "forwarded"


@pytest.mark.asyncio
async def test_copy_reupload_downloads_and_resends_byte_kinds(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = await copy_reupload(tg, manifest, workdir, "labacct", quiet)

    assert result["transport"] == "reupload"
    for kind in BYTE_FIXTURES:
        assert result["results"][kind] == "copied"
    assert result["results"]["album"] == "copied"
    for kind in ("text", *NON_BYTE_LAB_KINDS):
        assert result["results"][kind] == "not_applicable"
    assert tg.downloads  # media actually went through the download path
    assert not list(workdir.iterdir())  # workdir cleaned after the phase


@pytest.mark.asyncio
async def test_copy_phases_respect_kill_switch(tmp_path, monkeypatch):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    with pytest.raises(PolicyError):
        await copy_native(tg, manifest, "labacct", quiet)
    with pytest.raises(PolicyError):
        await copy_reupload(tg, manifest, tmp_path, "labacct", quiet)


# --- Task 6: teardown ---

from tgcli.mirror_lab import teardown_lab  # noqa: E402


class TeardownTG(TransportTG):
    def __init__(self, titles):
        super().__init__()
        self._titles = titles
        self.deleted = []

    async def get_entity(self, ref):
        peer_id = getattr(ref, "channel_id", ref)
        return NS(
            id=peer_id,
            title=self._titles.get(peer_id, ""),
            creator=True,
            broadcast=True,
            megagroup=False,
        )

    async def __call__(self, request):
        if isinstance(request, functions.channels.DeleteChannelRequest):
            self.deleted.append(request.channel)
            return NS(updates=[])
        return await super().__call__(request)


@pytest.mark.asyncio
async def test_teardown_deletes_only_marked_lab_channels(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    tg = TeardownTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    result = await teardown_lab(tg, manifest, path, "labacct", quiet)
    assert sorted(result["removed"]) == ["dest_native", "open_source"]
    assert len(tg.deleted) == 2
    assert load_manifest(path)["channels"] == {}


@pytest.mark.asyncio
async def test_teardown_refuses_channel_without_live_marker(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    tg = TeardownTG({100: "renamed innocent channel"})
    with pytest.raises(PolicyError):
        await teardown_lab(tg, manifest, tmp_path / "lab.json", "labacct", quiet)
    assert tg.deleted == []


@pytest.mark.asyncio
async def test_teardown_refuses_marked_channel_not_owned_by_account(tmp_path):
    class ForeignTeardownTG(TeardownTG):
        async def get_entity(self, ref):
            entity = await super().get_entity(ref)
            entity.creator = False
            return entity

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    tg = ForeignTeardownTG({100: lab_title(manifest, "open_source")})
    with pytest.raises(PolicyError, match="owned lab broadcast"):
        await teardown_lab(
            tg, manifest, tmp_path / "lab.json", "labacct", quiet
        )
    assert tg.deleted == []


@pytest.mark.asyncio
async def test_teardown_persists_progress_before_later_delete_failure(tmp_path):
    class SecondDeleteFailsTG(TeardownTG):
        async def __call__(self, request):
            if isinstance(request, functions.channels.DeleteChannelRequest):
                if len(self.deleted) == 1:
                    raise RuntimeError("second delete failed")
            return await super().__call__(request)

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    tg = SecondDeleteFailsTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })

    with pytest.raises(RuntimeError, match="second delete"):
        await teardown_lab(tg, manifest, path, "labacct", quiet)

    persisted = load_manifest(path)
    assert set(persisted["channels"]) == {"dest_native"}


@pytest.mark.asyncio
async def test_teardown_preserves_ambiguous_delete_until_confirmed(tmp_path):
    class AcceptThenCancelTG(TeardownTG):
        async def __call__(self, request):
            if isinstance(request, functions.channels.DeleteChannelRequest):
                self.deleted.append(request.channel)
                raise asyncio.CancelledError()
            return await super().__call__(request)

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    first = AcceptThenCancelTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })

    with pytest.raises(asyncio.CancelledError):
        await teardown_lab(first, manifest, path, "labacct", quiet)
    assert load_manifest(path)["channels"]["open_source"]["delete_state"] == "deleting"

    class RecoverTG(TeardownTG):
        async def get_entity(self, ref):
            peer_id = getattr(ref, "channel_id", ref)
            if peer_id == 100:
                raise ChannelPrivateError(request=None)
            return await super().get_entity(ref)

    persisted = load_manifest(path)
    second = RecoverTG({200: lab_title(manifest, "dest_native")})
    result = await teardown_lab(second, persisted, path, "labacct", quiet)
    assert result == {"removed": ["dest_native"], "unresolved": ["open_source"]}
    remaining = load_manifest(path)["channels"]
    assert set(remaining) == {"open_source"}
    assert remaining["open_source"]["delete_state"] == "deleting"


@pytest.mark.asyncio
async def test_every_lab_mutation_has_correlated_audit_result(tmp_path):
    from tgcli.safety import audit_path

    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    await copy_native(tg, manifest, "labacct", quiet)
    await copy_reupload(tg, manifest, tmp_path / "work", "labacct", quiet)
    titles = {
        entry["peer_id"]: entry["title"] for entry in manifest["channels"].values()
    }
    teardown = TeardownTG(titles)
    await teardown_lab(
        teardown, manifest, tmp_path / "lab.json", "labacct", quiet
    )

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    assert records
    assert all("operation_id" in record for record in records)
    operations = {}
    for record in records:
        operations.setdefault(record["operation_id"], []).append(record)
    assert all(
        [record["phase"] for record in pair] == ["attempt", "result"]
        for pair in operations.values()
    )
