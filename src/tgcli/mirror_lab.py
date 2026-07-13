from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import asyncio
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.mirror_probe import probe_message, write_report

LAB_MARKER = "tgcli-r1-lab"
MANIFEST_VERSION = 3
SCENARIO_CHECKPOINT_VERSION = 1
COMPATIBILITY_FINGERPRINT_VERSION = 1
EVIDENCE_TTL = timedelta(days=30)
SCENARIO_PHASES = (
    "preflight",
    "create",
    "seed",
    "mirror",
    "verify",
    "teardown",
    "complete",
)
SCENARIO_CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_version",
        "phase",
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
        "cleanup_obligations",
        "compatibility_fingerprint",
    }
)
CHANNEL_ROLES = (
    "protected_source",
    "open_source",
    "dest_native",
    "dest_reupload",
)


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    content_profile: str
    source_family: str
    source_protected: bool
    destination_family: str
    discussion_kind: str
    discussion_protected: bool | None


def _scenario_spec(key: str) -> ScenarioSpec:
    topology, protection = key.split(".", 1)
    if topology.startswith("channel_"):
        channel_protection, discussion_protection = protection.split("_", 1)
        return ScenarioSpec(
            key=key,
            content_profile="sentinels",
            source_family="channel",
            source_protected=channel_protection == "protected",
            destination_family="channel",
            discussion_kind=topology.removeprefix("channel_"),
            discussion_protected=discussion_protection == "protected",
        )
    return ScenarioSpec(
        key=key,
        content_profile="full",
        source_family=topology,
        source_protected=protection == "protected",
        destination_family="supergroup" if topology == "basic" else topology,
        discussion_kind="none",
        discussion_protected=None,
    )


_REQUIRED_SCENARIOS = tuple(
    _scenario_spec(key)
    for key in (
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
)


def required_scenarios() -> tuple[ScenarioSpec, ...]:
    return _REQUIRED_SCENARIOS


def required_scenario_domains(spec: ScenarioSpec) -> tuple[str, ...]:
    domains = (
        "content",
        "structure",
        "attribution",
        "transport",
        "audit",
        "cleanup",
    )
    if spec.source_family == "forum" or spec.discussion_kind == "forum":
        domains += ("topic",)
    if spec.discussion_kind in {"plain", "forum"}:
        domains += ("comment_root",)
    return domains


def new_compatibility_fingerprint(
    scenario_key: str,
    *,
    fixture_schema_version: int,
    lab_code_digest: str,
    mirror_code_digest: str,
    telethon_version: str,
    telegram_schema_layer: int,
    account_role_binding: dict,
    config_digest: str,
) -> dict:
    return {
        "fingerprint_version": COMPATIBILITY_FINGERPRINT_VERSION,
        "scenario_key": scenario_key,
        "fixture_schema_version": fixture_schema_version,
        "lab_code_digest": lab_code_digest,
        "mirror_code_digest": mirror_code_digest,
        "telethon_version": telethon_version,
        "telegram_schema_layer": telegram_schema_layer,
        "account_role_binding": {
            role: dict(binding) for role, binding in account_role_binding.items()
        },
        "config_digest": config_digest,
    }


def new_scenario_checkpoint(scenario_key: str, fingerprint: dict) -> dict:
    if fingerprint.get("scenario_key") != scenario_key:
        raise ValueError(f"fingerprint scenario key mismatch: {scenario_key}")
    fingerprint_copy = deepcopy(fingerprint)
    return {
        "checkpoint_version": SCENARIO_CHECKPOINT_VERSION,
        "phase": "preflight",
        "created_peers": {},
        "created_topics": {},
        "outbound_operations": {},
        "verdicts": {},
        "cleanup_obligations": [],
        "compatibility_fingerprint": fingerprint_copy,
    }


def new_manifest(account_user_id: int) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "account_user_id": account_user_id,
        "lab_id": secrets.token_hex(12),
        "created_at": datetime.now(UTC).isoformat(),
        "channels": {},
        "creating": {},
        "seeded": {},
        "blocked": {},
        "scenarios": {},
    }


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_compatibility_fingerprint(
    scenario_key: str,
    fingerprint: object,
    *,
    allow_version_mismatch: bool = False,
) -> None:
    fields = {
        "fingerprint_version",
        "scenario_key",
        "fixture_schema_version",
        "lab_code_digest",
        "mirror_code_digest",
        "telethon_version",
        "telegram_schema_layer",
        "account_role_binding",
        "config_digest",
    }
    if not isinstance(fingerprint, dict) or set(fingerprint) != fields:
        raise ValueError(f"invalid compatibility fingerprint fields: {scenario_key}")
    if (
        type(fingerprint["fingerprint_version"]) is not int
        or fingerprint["fingerprint_version"] <= 0
        or (
            not allow_version_mismatch
            and fingerprint["fingerprint_version"]
            != COMPATIBILITY_FINGERPRINT_VERSION
        )
    ):
        raise ValueError(f"unsupported fingerprint version: {scenario_key}")
    if (
        not isinstance(fingerprint["scenario_key"], str)
        or not fingerprint["scenario_key"]
        or fingerprint["scenario_key"] != scenario_key
    ):
        raise ValueError(f"fingerprint scenario key mismatch: {scenario_key}")
    if not _is_positive_int(fingerprint["fixture_schema_version"]):
        raise ValueError(f"invalid fixture schema version: {scenario_key}")
    if not _is_positive_int(fingerprint["telegram_schema_layer"]):
        raise ValueError(f"invalid Telegram schema layer: {scenario_key}")
    for field in ("lab_code_digest", "mirror_code_digest", "config_digest"):
        digest = fingerprint[field]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"invalid {field}: {scenario_key}")
    telethon_version = fingerprint["telethon_version"]
    if not isinstance(telethon_version, str) or not telethon_version:
        raise ValueError(f"invalid Telethon version: {scenario_key}")
    roles = fingerprint["account_role_binding"]
    if (
        not isinstance(roles, dict)
        or "operator" not in roles
        or not set(roles) <= {"operator", "lab_peer"}
    ):
        raise ValueError(f"invalid account role binding: {scenario_key}")
    role_ids = []
    for role, binding in roles.items():
        if not isinstance(binding, dict) or set(binding) != {"alias", "user_id"}:
            raise ValueError(f"invalid account role: {scenario_key}.{role}")
        if not isinstance(binding["alias"], str) or not binding["alias"]:
            raise ValueError(f"invalid account alias: {scenario_key}.{role}")
        if not _is_positive_int(binding["user_id"]):
            raise ValueError(f"invalid account user id: {scenario_key}.{role}")
        role_ids.append(binding["user_id"])
    if len(role_ids) != len(set(role_ids)):
        raise ValueError(f"duplicate account role user id: {scenario_key}")


def _validate_scenario_checkpoint_envelope(
    scenario_key: str,
    checkpoint: object,
    *,
    bind_fingerprint_to_scenario: bool,
) -> None:
    if not isinstance(checkpoint, dict) or set(checkpoint) != SCENARIO_CHECKPOINT_FIELDS:
        raise ValueError(f"invalid scenario checkpoint fields: {scenario_key}")
    if (
        type(checkpoint["checkpoint_version"]) is not int
        or checkpoint["checkpoint_version"] != SCENARIO_CHECKPOINT_VERSION
    ):
        raise ValueError(f"unsupported checkpoint version: {scenario_key}")
    if checkpoint["phase"] not in SCENARIO_PHASES:
        raise ValueError(f"invalid scenario phase: {scenario_key}")
    for field in (
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
    ):
        if not isinstance(checkpoint[field], dict):
            raise ValueError(f"invalid {field}: {scenario_key}")
    if not isinstance(checkpoint["cleanup_obligations"], list):
        raise ValueError(f"invalid cleanup obligations: {scenario_key}")
    fingerprint = checkpoint["compatibility_fingerprint"]
    fingerprint_key = scenario_key
    if not bind_fingerprint_to_scenario:
        fingerprint_key = (
            fingerprint.get("scenario_key") if isinstance(fingerprint, dict) else ""
        )
    _validate_compatibility_fingerprint(
        fingerprint_key,
        fingerprint,
        allow_version_mismatch=not bind_fingerprint_to_scenario,
    )


def compare_compatibility_fingerprints(expected: dict, actual: dict) -> dict:
    expected_key = expected.get("scenario_key") if isinstance(expected, dict) else ""
    _validate_compatibility_fingerprint(expected_key, expected)
    actual_key = actual.get("scenario_key") if isinstance(actual, dict) else ""
    _validate_compatibility_fingerprint(
        actual_key,
        actual,
        allow_version_mismatch=True,
    )
    fields = (
        "fingerprint_version",
        "scenario_key",
        "fixture_schema_version",
        "lab_code_digest",
        "mirror_code_digest",
        "telethon_version",
        "telegram_schema_layer",
        "account_role_binding",
        "config_digest",
    )
    reasons = [
        f"{field}_mismatch" for field in fields if expected[field] != actual[field]
    ]
    return {"compatible": not reasons, "reasons": reasons}


def classify_scenario_cell(
    scenario_key: str,
    checkpoint: dict | None,
    expected_fingerprint: dict,
    *,
    now: datetime | None,
    last_observed_at: datetime | None = None,
) -> dict:
    _validate_compatibility_fingerprint(scenario_key, expected_fingerprint)
    spec = next((spec for spec in required_scenarios() if spec.key == scenario_key), None)
    if spec is None:
        raise ValueError(f"unknown scenario key: {scenario_key}")
    if checkpoint is None:
        return {
            "scenario_key": scenario_key,
            "status": "missing",
            "freshness": "missing",
            "machine_compatible": False,
            "reasons": ["checkpoint_missing"],
            "fingerprint_reasons": [],
            "completed_at": None,
            "expires_at": None,
            "domains": {},
            "cleanup": "missing",
        }
    if not isinstance(checkpoint, dict):
        raise ValueError(f"invalid scenario checkpoint: {scenario_key}")
    _validate_scenario_checkpoint_envelope(
        scenario_key,
        checkpoint,
        bind_fingerprint_to_scenario=False,
    )
    comparison = compare_compatibility_fingerprints(
        expected_fingerprint,
        checkpoint.get("compatibility_fingerprint"),
    )
    required_domains = required_scenario_domains(spec)
    reasons = list(comparison["reasons"])
    blocked = False
    red = False

    verdicts = checkpoint.get("verdicts")
    if not isinstance(verdicts, dict) or set(verdicts) != {"completed_at", "domains"}:
        reasons.append("result_metadata_invalid")
        blocked = True
        verdicts = {}
    raw_completed_at = verdicts.get("completed_at")
    completed_at_valid = (
        isinstance(raw_completed_at, datetime)
        and raw_completed_at.tzinfo is not None
        and raw_completed_at.utcoffset() is not None
    )
    completed_at = raw_completed_at if completed_at_valid else None
    expires_at = completed_at + EVIDENCE_TTL if completed_at is not None else None

    raw_domains = verdicts.get("domains")
    if not isinstance(raw_domains, dict):
        if "result_metadata_invalid" not in reasons:
            reasons.append("result_metadata_invalid")
        blocked = True
        domains = {}
    else:
        domains = dict(raw_domains)
        unknown_domains = set(domains) - set(required_domains)
        if unknown_domains:
            raise ValueError(f"unknown scenario domains: {scenario_key}")
        if any(value not in {"green", "red", "blocked"} for value in domains.values()):
            raise ValueError(f"invalid scenario domain status: {scenario_key}")
    for domain in required_domains:
        state = domains.get(domain)
        if state is None:
            reasons.append(f"domain_missing:{domain}")
            blocked = True
        elif state == "blocked":
            reasons.append(f"domain_blocked:{domain}")
            blocked = True
        elif state == "red":
            reasons.append(f"domain_red:{domain}")
            red = True

    phase = checkpoint.get("phase")
    obligations = checkpoint.get("cleanup_obligations")
    cleanup_pending = phase == "teardown" or (
        isinstance(obligations, list) and bool(obligations)
    )
    if cleanup_pending:
        cleanup = "pending"
        reasons.append("cleanup_pending")
    elif not isinstance(obligations, list):
        cleanup = "blocked"
        reasons.append("cleanup_blocked")
        blocked = True
    elif phase != "complete" or domains.get("cleanup") != "green":
        cleanup = "blocked"
        reasons.append("cleanup_blocked")
        blocked = True
    else:
        cleanup = "green"
    if phase not in {"complete", "teardown"}:
        reasons.append("phase_incomplete")
        blocked = True

    clock_reasons = []
    now_valid = (
        isinstance(now, datetime)
        and now.tzinfo is not None
        and now.utcoffset() is not None
    )
    observed_valid = (
        isinstance(last_observed_at, datetime)
        and last_observed_at.tzinfo is not None
        and last_observed_at.utcoffset() is not None
    )
    if now is None:
        clock_reasons.append("clock_unavailable")
    elif not now_valid:
        clock_reasons.append("clock_invalid")
    if not completed_at_valid and "clock_invalid" not in clock_reasons:
        clock_reasons.append("clock_invalid")
    if last_observed_at is not None and not observed_valid:
        if "clock_invalid" not in clock_reasons:
            clock_reasons.append("clock_invalid")
    if now_valid and completed_at_valid and now < raw_completed_at:
        clock_reasons.append("clock_before_completion")
    if now_valid and observed_valid and now < last_observed_at:
        clock_reasons.append("clock_moved_backward")
    if clock_reasons:
        freshness = "blocked"
        blocked = True
        reasons.extend(clock_reasons)
    elif now >= expires_at:
        freshness = "stale"
        reasons.append("evidence_expired")
    else:
        freshness = "fresh"

    if comparison["reasons"] or freshness == "stale":
        status = "stale"
    elif cleanup_pending:
        status = "cleanup_pending"
    elif blocked:
        status = "blocked"
    elif red:
        status = "red"
    else:
        status = "machine_compatible"
    return {
        "scenario_key": scenario_key,
        "status": status,
        "freshness": freshness,
        "machine_compatible": status == "machine_compatible",
        "reasons": reasons,
        "fingerprint_reasons": comparison["reasons"],
        "completed_at": completed_at,
        "expires_at": expires_at,
        "domains": domains,
        "cleanup": cleanup,
    }


def classify_scenario_matrix(
    checkpoints: dict[str, dict],
    expected_fingerprints: dict[str, dict],
    *,
    now: datetime | None,
    last_observed_at: datetime | None = None,
) -> dict:
    if not isinstance(checkpoints, dict) or not isinstance(expected_fingerprints, dict):
        raise ValueError("scenario matrix inputs must be objects")
    scenario_keys = tuple(spec.key for spec in required_scenarios())
    required_keys = set(scenario_keys)
    if set(expected_fingerprints) != required_keys:
        raise ValueError("expected fingerprints must cover every required scenario")
    if not set(checkpoints) <= required_keys:
        raise ValueError("unknown scenario checkpoint key")
    for scenario_key, fingerprint in expected_fingerprints.items():
        _validate_compatibility_fingerprint(scenario_key, fingerprint)
    for scenario_key, checkpoint in checkpoints.items():
        if (
            not isinstance(checkpoint, dict)
            or not isinstance(checkpoint.get("compatibility_fingerprint"), dict)
            or checkpoint["compatibility_fingerprint"].get("scenario_key")
            != scenario_key
        ):
            raise ValueError(f"checkpoint scenario key mismatch: {scenario_key}")

    cells = {
        scenario_key: classify_scenario_cell(
            scenario_key,
            checkpoints.get(scenario_key),
            expected_fingerprints[scenario_key],
            now=now,
            last_observed_at=last_observed_at,
        )
        for scenario_key in scenario_keys
    }
    statuses = (
        "missing",
        "stale",
        "blocked",
        "red",
        "cleanup_pending",
        "machine_compatible",
    )
    counts = {
        status: sum(cell["status"] == status for cell in cells.values())
        for status in statuses
    }
    non_green_cells = [
        scenario_key
        for scenario_key in scenario_keys
        if cells[scenario_key]["status"] != "machine_compatible"
        or cells[scenario_key]["cleanup"] != "green"
    ]
    return {
        "machine_green": not non_green_cells,
        "cells": cells,
        "counts": counts,
        "non_green_cells": non_green_cells,
    }


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or type(data.get("manifest_version")) is not int
        or data["manifest_version"] not in {2, 3}
    ):
        raise ValueError("unsupported lab manifest")
    source_version = data["manifest_version"]
    if source_version == 2 and "scenarios" in data:
        raise ValueError("v2 manifest must not contain scenarios")
    if source_version == 3 and not isinstance(data.get("scenarios"), dict):
        raise ValueError("v3 manifest requires scenarios object")
    for key in ("account_user_id", "lab_id", "channels", "seeded"):
        if key not in data:
            raise ValueError(f"lab manifest missing {key!r}")
    if not _is_positive_int(data["account_user_id"]):
        raise ValueError("invalid lab account user id")
    if not isinstance(data["channels"], dict):
        raise ValueError("invalid lab channels")
    if not isinstance(data["seeded"], dict):
        raise ValueError("invalid lab seeded state")
    lab_id = data["lab_id"]
    if (
        not isinstance(lab_id, str)
        or len(lab_id) != 24
        or any(char not in "0123456789abcdef" for char in lab_id)
    ):
        raise ValueError("invalid lab id")
    seen_peer_ids = set()
    for role, channel in data["channels"].items():
        if role not in CHANNEL_ROLES:
            raise ValueError(f"unknown lab channel role: {role}")
        if not isinstance(channel, dict):
            raise ValueError(f"invalid lab channel entry: {role}")
        peer_id = channel.get("peer_id")
        title = channel.get("title")
        if not _is_positive_int(peer_id):
            raise ValueError(f"invalid lab peer id: {role}")
        if title != lab_title(data, role):
            raise ValueError(f"lab channel title does not match provenance: {role}")
        if peer_id in seen_peer_ids:
            raise ValueError(f"duplicate lab peer id: {peer_id}")
        seen_peer_ids.add(peer_id)
    blocked = data.setdefault("blocked", {})
    if not isinstance(blocked, dict):
        raise ValueError("invalid blocked state")
    creating = data.setdefault("creating", {})
    if not isinstance(creating, dict):
        raise ValueError("invalid creating state")
    for role, pending in creating.items():
        if role not in CHANNEL_ROLES or not isinstance(pending, dict):
            raise ValueError(f"invalid creating role: {role}")
        if role in data["channels"]:
            raise ValueError(f"creating role already has a channel: {role}")
        if pending.get("title") != lab_title(data, role):
            raise ValueError(f"invalid creating title: {role}")
        if pending.get("state") not in {"dispatching", "ambiguous"}:
            raise ValueError(f"invalid creating state: {role}")
    if source_version == 2:
        data["manifest_version"] = MANIFEST_VERSION
        data["scenarios"] = {}
    allowed_scenarios = {spec.key for spec in required_scenarios()}
    for scenario_key, checkpoint in data["scenarios"].items():
        if scenario_key not in allowed_scenarios:
            raise ValueError(f"unknown scenario key: {scenario_key}")
        _validate_scenario_checkpoint_envelope(
            scenario_key,
            checkpoint,
            bind_fingerprint_to_scenario=True,
        )
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    write_report(path, manifest)


def lab_title(manifest: dict, role: str) -> str:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    return f"{LAB_MARKER} {manifest['lab_id']} {role}"


def record_channel(manifest: dict, role: str, peer_id: int, title: str) -> None:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    if title != lab_title(manifest, role):
        raise PolicyError(f"channel title lacks exact lab provenance: {title!r}")
    manifest["channels"][role] = {"peer_id": peer_id, "title": title}


def lab_peer_ids(manifest: dict) -> set[int]:
    return {entry["peer_id"] for entry in manifest["channels"].values()}


def assert_lab_peer(manifest: dict, peer_id: int) -> None:
    if peer_id not in lab_peer_ids(manifest):
        raise PolicyError(f"peer {peer_id} is not a lab channel; refusing mutation")


def record_seed(manifest: dict, role: str, kind: str, message_ids: list[int]) -> None:
    manifest["seeded"].setdefault(role, {})[kind] = list(message_ids)


def record_blocked(manifest: dict, role: str, kind: str, error: str) -> None:
    manifest.setdefault("blocked", {}).setdefault(role, {})[kind] = error


def seeded_ids(manifest: dict, role: str) -> dict[str, list[int]]:
    return dict(manifest["seeded"].get(role, {}))


# --- Task 2: fixture matrix and payload generators ---

import hashlib
import struct
import zlib
from typing import Callable

from telethon.tl import types


def build_png(color: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(
            ">I", zlib.crc32(block)
        )

    width = height = 4
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(color) * width
    body = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", body)
        + chunk(b"IEND", b"")
    )


def deterministic_bytes(seed: str, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(f"{seed}:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:size])


@dataclass(frozen=True)
class ByteFixture:
    kind: str
    filename: str
    payload: Callable[[], bytes]
    attributes: Callable[[], list]
    force_document: bool
    reupload_fidelity: str  # "exact" | "reencoded"
    mime_type: str


BYTE_FIXTURES: dict[str, ByteFixture] = {
    "photo": ByteFixture(
        kind="photo",
        filename="lab-photo.jpg",
        payload=lambda: build_png((0, 0, 255)),
        attributes=lambda: [],
        force_document=False,
        reupload_fidelity="reencoded",
        mime_type="image/jpeg",
    ),
    "document": ByteFixture(
        kind="document",
        filename="lab-document.txt",
        payload=lambda: deterministic_bytes("document", 16_384),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-document.txt"),
        ],
        force_document=True,
        reupload_fidelity="exact",
        mime_type="text/plain",
    ),
    "audio": ByteFixture(
        kind="audio",
        filename="lab-audio.mp3",
        payload=lambda: deterministic_bytes("audio", 32_768),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-audio.mp3"),
            types.DocumentAttributeAudio(
                duration=3, title="Lab audio", performer="tgcli"
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="audio/mpeg",
    ),
    "voice": ByteFixture(
        kind="voice",
        filename="lab-voice.ogg",
        payload=lambda: deterministic_bytes("voice", 8_192),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-voice.ogg"),
            types.DocumentAttributeAudio(duration=2, voice=True),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="audio/ogg",
    ),
    "video": ByteFixture(
        kind="video",
        filename="lab-video.mp4",
        payload=lambda: deterministic_bytes("video", 1_600_000),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-video.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=64, h=64, supports_streaming=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="video/mp4",
    ),
    "video_note": ByteFixture(
        kind="video_note",
        filename="lab-note.mp4",
        payload=lambda: deterministic_bytes("video_note", 24_576),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-note.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=240, h=240, round_message=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="video/mp4",
    ),
    "animation": ByteFixture(
        kind="animation",
        filename="lab-animation.gif",
        payload=lambda: deterministic_bytes("animation", 20_480),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-animation.gif"),
            types.DocumentAttributeAnimated(),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="image/gif",
    ),
    "sticker": ByteFixture(
        kind="sticker",
        filename="lab-sticker.webp",
        payload=lambda: deterministic_bytes("sticker", 4_096),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-sticker.webp"),
            types.DocumentAttributeSticker(
                alt="🙂", stickerset=types.InputStickerSetEmpty()
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="image/webp",
    ),
}

NON_BYTE_LAB_KINDS = (
    "contact", "dice", "geo", "poll", "venue",
)

EXCLUDED_KINDS: dict[str, str] = {
    "game": "requires a bot-owned game",
    "giveaway": "requires a Premium boost purchase with real cost",
    "giveaway_results": "exists only after a finished giveaway",
    "geo_live": "native channel forward degrades live location to static geo",
    "invoice": "requires a payment-enabled bot",
    "paid_media_preview": "requires a monetization-enabled channel",
    "paid_media_revealed": "requires a monetization-enabled channel",
    "story": "excluded by ADR-0013 fidelity target; channel stories need boosts",
    "todo": "broadcast channels rejected InputMediaTodo with MediaInvalidError in R1",
}

COVERED_BY_R0 = frozenset({"text", "webpage", "service", "empty", "unsupported"})

ALBUM_COLORS = ((255, 0, 0), (0, 255, 0))


def preflight_fixture_tools() -> dict[str, str]:
    tools = {name: shutil.which(name) for name in ("ffmpeg", "cwebp")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise ValueError(f"lab fixture tool unavailable: {', '.join(missing)}")
    encoders = subprocess.run(
        [tools["ffmpeg"], "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    required = (
        "libmp3lame", "libopus", "libx264", "aac", "png", "mjpeg", "gif"
    )
    unavailable = [encoder for encoder in required if encoder not in encoders]
    if unavailable:
        raise ValueError(
            f"lab fixture encoder unavailable: {', '.join(unavailable)}"
        )
    return {name: path for name, path in tools.items() if path is not None}


def _run_fixture_command(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip().splitlines()[-1] if exc.stderr else "command failed"
        raise ValueError(f"lab fixture generation failed: {detail}") from exc


def materialize_fixture(
    kind: str,
    directory: Path,
    *,
    color: tuple[int, int, int] = (0, 0, 255),
) -> Path:
    fixture = BYTE_FIXTURES[kind]
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / fixture.filename
    if kind == "document":
        output.write_bytes(deterministic_bytes("document", 16_384))
        return output

    tools = preflight_fixture_tools()
    common = [
        tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    ]
    if kind == "photo":
        hex_color = "".join(f"{component:02x}" for component in color)
        command = common + [
            "-f", "lavfi", "-i", f"color=c=0x{hex_color}:s=512x512:d=1",
            "-frames:v", "1", "-map_metadata", "-1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact", "-threads", "1", "-c:v", "mjpeg",
            "-q:v", "2", str(output),
        ]
    elif kind == "audio":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
            "-id3v2_version", "0", "-write_xing", "0", str(output),
        ]
    elif kind == "voice":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=2",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-vbr", "off",
            "-application", "voip", "-frame_duration", "20", "-serial_offset", "137",
            str(output),
        ]
    elif kind in {"video", "video_note"}:
        settings = {
            "video": ("640x360", "30", "2", "0"),
            "video_note": ("240x240", "24", "2", "23"),
        }
        size, rate, duration, crf = settings[kind]
        inputs = [
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}",
        ]
        audio = []
        if kind == "video":
            inputs += [
                "-f", "lavfi", "-i",
                "sine=frequency=330:sample_rate=48000:duration=2",
            ]
            audio = ["-c:a", "aac", "-b:a", "64k", "-shortest"]
        else:
            audio = ["-an"]
        command = common + inputs + [
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", crf,
            "-pix_fmt", "yuv420p", "-threads", "1", "-x264-params",
            "threads=1:lookahead_threads=1:sync-lookahead=0:force-cfr=1",
            "-movflags", "+faststart",
        ] + audio + [str(output)]
    elif kind == "animation":
        command = common + [
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=15:duration=1",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-an", "-c:v", "gif", "-threads", "1", str(output),
        ]
    elif kind == "sticker":
        png = directory / "lab-sticker.png"
        _run_fixture_command(common + [
            "-f", "lavfi", "-i", "color=c=0x3366cc:s=512x512:d=1",
            "-frames:v", "1", "-map_metadata", "-1", "-fflags", "+bitexact",
            str(png),
        ])
        _run_fixture_command([
            tools["cwebp"], "-quiet", "-lossless", "-m", "6", "-o", str(output), str(png),
        ])
        png.unlink()
        return output
    else:
        raise ValueError(f"unknown byte fixture: {kind}")
    _run_fixture_command(command)
    return output


def non_byte_media() -> dict[str, object]:
    def geo_point() -> types.InputGeoPoint:
        return types.InputGeoPoint(lat=59.93, long=30.31)

    return {
        "poll": types.InputMediaPoll(
            poll=types.Poll(
                id=0,
                hash=0,
                question=types.TextWithEntities(text="lab poll", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="A", entities=[]),
                        option=b"0",
                    ),
                    types.PollAnswer(
                        text=types.TextWithEntities(text="B", entities=[]),
                        option=b"1",
                    ),
                ],
            )
        ),
        "contact": types.InputMediaContact(
            phone_number="+10000000000",
            first_name="Lab",
            last_name="Fixture",
            vcard="",
        ),
        "geo": types.InputMediaGeoPoint(geo_point=geo_point()),
        "venue": types.InputMediaVenue(
            geo_point=geo_point(),
            title="Lab venue",
            address="Lab address",
            provider="lab",
            venue_id="lab-1",
            venue_type="lab",
        ),
        "dice": types.InputMediaDice(emoticon="🎲"),
    }


def planned_kinds() -> list[str]:
    return ["text", *sorted(BYTE_FIXTURES), *NON_BYTE_LAB_KINDS, "album"]


def pending_kinds(manifest: dict, role: str) -> list[str]:
    done = set(manifest["seeded"].get(role, {}))
    return [kind for kind in planned_kinds() if kind not in done]


# --- Task 3: verdict and transport-fidelity comparison ---

from tgcli.mirror_probe import NON_BYTE_KINDS


def lab_verdict(report: dict, manifest: dict, role: str) -> dict:
    schema_ok = report.get("probe_version") == 2
    seeded = manifest["seeded"].get(role, {})
    planned = set(planned_kinds())
    blocked = dict(manifest.get("blocked", {}).get(role, {}))
    pending = sorted(planned - set(seeded) - set(blocked))
    expected = planned - {"album"} - set(blocked)
    observed = {row["kind"]: row for row in report["capabilities"]}
    missing = sorted(expected - set(observed))
    failing = sorted(
        kind
        for kind, row in observed.items()
        if kind in expected
        and not (
            row["telethon_bytes"] == "pass"
            or (
                kind in NON_BYTE_KINDS
                and all(sample["decode"] == "pass" for sample in row["samples"])
            )
        )
    )
    green = schema_ok and not blocked and not missing and not failing and not pending
    return {
        "verdict": "green" if green else "red",
        "schema": "pass" if schema_ok else "unsupported",
        "missing": missing,
        "failing": failing,
        "pending": pending,
        "blocked": blocked,
        "excluded": dict(EXCLUDED_KINDS),
        "covered_by_r0": sorted(COVERED_BY_R0),
    }


def _kind_shas(report: dict) -> dict[str, list[str]]:
    return {
        row["kind"]: sorted(
            sample["sha256"] for sample in row["samples"] if sample["sha256"]
        )
        for row in report["capabilities"]
    }


async def album_groups(tg, entity, *, limit: int) -> list[dict]:
    groups: dict[int, list] = {}
    async for message in tg.iter_messages(entity, limit=limit):
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is not None:
            groups.setdefault(grouped_id, []).append(message)
    result = []
    ordered_groups = sorted(
        (messages for messages in groups.values() if len(messages) > 1),
        key=lambda messages: min(message.id for message in messages),
    )
    for messages in ordered_groups:
        samples = [
            await probe_message(tg, message)
            for message in sorted(messages, key=lambda message: message.id)
        ]
        result.append(
            {
                "count": len(samples),
                "sha256": [sample["sha256"] for sample in samples],
                "telethon_bytes": [sample["telethon_bytes"] for sample in samples],
            }
        )
    return result


def compare_transport(
    source_report: dict,
    dest_report: dict,
    *,
    transport: str,
    expected_kinds: set[str] | None = None,
) -> dict:
    if transport not in {"native", "reupload"}:
        raise ValueError(f"unknown transport: {transport}")
    schema_ok = (
        source_report.get("probe_version") == 2
        and dest_report.get("probe_version") == 2
    )
    source = {row["kind"]: row for row in source_report["capabilities"]}
    dest = {row["kind"]: row for row in dest_report["capabilities"]}
    source_shas = _kind_shas(source_report)
    dest_shas = _kind_shas(dest_report)

    rows = []
    kinds = (
        expected_kinds
        if expected_kinds is not None
        else (set(source) & set(BYTE_FIXTURES))
    )
    for kind in sorted(kinds):
        if kind == "album":
            source_groups = source_report.get("album_groups", [])
            dest_groups = dest_report.get("album_groups", [])

            def valid(groups):
                return bool(groups) and all(
                    group["count"] == len(group["sha256"])
                    == len(group["telethon_bytes"])
                    and all(state == "pass" for state in group["telethon_bytes"])
                    and None not in group["sha256"]
                    and len(set(group["sha256"])) == group["count"]
                    for group in groups
                )

            matched = (
                valid(source_groups)
                and valid(dest_groups)
                and source_groups == dest_groups
            )
            rows.append(
                {
                    "kind": kind,
                    "expectation": "grouped",
                    "result": "pass" if matched else "fail",
                }
            )
            continue
        expectation = "exact"
        if transport == "reupload":
            expectation = BYTE_FIXTURES.get(
                kind, BYTE_FIXTURES["document"]
            ).reupload_fidelity
        if kind not in source:
            rows.append({"kind": kind, "expectation": expectation, "result": "missing"})
            continue
        if kind not in dest:
            rows.append({"kind": kind, "expectation": expectation, "result": "missing"})
            continue
        if kind not in BYTE_FIXTURES:
            matched = (
                source[kind].get("sample_count") == dest[kind].get("sample_count")
                and all(
                    sample.get("decode") == "pass"
                    for sample in source[kind].get("samples", [])
                    + dest[kind].get("samples", [])
                )
            )
        elif expectation == "exact":
            matched = (
                source[kind].get("telethon_bytes") == "pass"
                and dest[kind].get("telethon_bytes") == "pass"
                and bool(source_shas[kind])
                and source_shas[kind] == dest_shas.get(kind)
            )
        else:
            matched = (
                source[kind].get("telethon_bytes") == "pass"
                and dest[kind].get("telethon_bytes") == "pass"
                and len(dest_shas.get(kind, [])) == len(source_shas[kind])
            )
        rows.append(
            {
                "kind": kind,
                "expectation": expectation,
                "result": "pass" if matched else "fail",
            }
        )
    green = schema_ok and rows and all(row["result"] == "pass" for row in rows)
    return {
        "transport": transport,
        "verdict": "green" if green else "red",
        "schema": "pass" if schema_ok else "unsupported",
        "rows": rows,
    }


# --- Task 4: channel provisioning and seeding engines ---

import os

from telethon.tl import functions

from tgcli.safety import append_audit, enforce_mutation_allowed


async def _audited_mutation(
    action, account_alias, details, mutation, *, ambiguous_errors=False
):
    enforce_mutation_allowed(readonly=False)
    operation_id = secrets.token_hex(12)
    append_audit(
        action,
        account_alias,
        {**details, "operation_id": operation_id, "phase": "attempt"},
    )
    try:
        result = await mutation()
    except BaseException as exc:
        status = (
            "ambiguous"
            if ambiguous_errors or isinstance(exc, asyncio.CancelledError)
            else "failed"
        )
        append_audit(
            action,
            account_alias,
            {
                **details,
                "operation_id": operation_id,
                "phase": "result",
                "status": status,
                "error": type(exc).__name__,
            },
        )
        raise
    append_audit(
        action,
        account_alias,
        {
            **details,
            "operation_id": operation_id,
            "phase": "result",
            "status": "confirmed",
        },
    )
    return result


def _random_id() -> int:
    return int.from_bytes(os.urandom(8), "little", signed=True)


def _sent_message_id(update) -> int:
    for item in getattr(update, "updates", ()):
        message = getattr(item, "message", None)
        if message is not None and hasattr(message, "id"):
            return message.id
    for item in getattr(update, "updates", ()):
        if type(item).__name__ == "UpdateMessageID":
            return item.id
    raise ValueError("could not extract sent message id from update")


async def _lab_entity(tg, manifest: dict, role: str):
    channel = manifest["channels"][role]
    assert_lab_peer(manifest, channel["peer_id"])
    entity = await tg.get_entity(types.PeerChannel(channel["peer_id"]))
    if (
        getattr(entity, "title", None) != channel["title"]
        or not getattr(entity, "creator", False)
        or not getattr(entity, "broadcast", False)
        or getattr(entity, "megagroup", False)
    ):
        raise PolicyError(f"{role}: peer is not the expected owned lab broadcast")
    return entity


async def _find_ambiguous_created_channel(tg, title: str):
    matches = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if (
            getattr(entity, "title", None) == title
            and getattr(entity, "creator", False)
            and getattr(entity, "broadcast", False)
            and not getattr(entity, "megagroup", False)
        ):
            matches.append(entity)
    if len(matches) > 1:
        raise PolicyError("ambiguous lab create matched multiple owned channels")
    return matches[0] if matches else None


async def create_lab_channels(tg, manifest, manifest_path, account_alias, note) -> dict:
    enforce_mutation_allowed(readonly=False)
    manifest.setdefault("creating", {})
    for role in CHANNEL_ROLES:
        if role in manifest["channels"]:
            if role == "protected_source":
                entity = await _lab_entity(tg, manifest, role)
                if (
                    not manifest["channels"][role].get("protection_enabled", False)
                    or not getattr(entity, "noforwards", False)
                ):
                    await _audited_mutation(
                        "mirror-lab-protect",
                        account_alias,
                        {"role": role},
                        lambda: tg(
                            functions.messages.ToggleNoForwardsRequest(
                                peer=entity,
                                enabled=True,
                            )
                        ),
                    )
                    manifest["channels"][role]["protection_enabled"] = True
                    save_manifest(manifest_path, manifest)
                    note(f"{role}: protection enabled")
                else:
                    note(f"{role}: already created, skipping")
            else:
                note(f"{role}: already created, skipping")
            continue
        title = lab_title(manifest, role)
        pending = manifest["creating"].get(role)
        if pending:
            channel = await _find_ambiguous_created_channel(tg, pending["title"])
            if channel is None:
                pending["state"] = "ambiguous"
                save_manifest(manifest_path, manifest)
                raise PolicyError(
                    f"{role}: ambiguous create is not yet visible; refusing duplicate"
                )
        else:
            manifest["creating"][role] = {"title": title, "state": "dispatching"}
            save_manifest(manifest_path, manifest)
            try:
                update = await _audited_mutation(
                    "mirror-lab-create",
                    account_alias,
                    {"role": role, "title": title},
                    lambda: tg(
                        functions.channels.CreateChannelRequest(
                            title=title,
                            about="tgcli R1 disposable lab channel",
                            broadcast=True,
                            megagroup=False,
                        )
                    ),
                    ambiguous_errors=True,
                )
            except BaseException:
                manifest["creating"][role]["state"] = "ambiguous"
                save_manifest(manifest_path, manifest)
                raise
            channel = update.chats[0]
        record_channel(manifest, role, channel.id, title)
        del manifest["creating"][role]
        save_manifest(manifest_path, manifest)
        if role == "protected_source":
            await _audited_mutation(
                "mirror-lab-protect",
                account_alias,
                {"role": role},
                lambda: tg(
                    functions.messages.ToggleNoForwardsRequest(
                        peer=channel, enabled=True
                    )
                ),
            )
            manifest["channels"][role]["protection_enabled"] = True
            save_manifest(manifest_path, manifest)
        note(f"{role}: created lab channel")
    return manifest


async def _seed_kind(tg, entity, kind: str, fixture_dir: Path) -> list[int]:
    if kind == "text":
        message = await tg.send_message(entity, "lab text fixture")
        return [message.id]
    if kind == "album":
        files = [
            materialize_fixture(
                "photo", fixture_dir / f"album-{index}", color=color
            )
            for index, color in enumerate(ALBUM_COLORS)
        ]
        messages = await tg.send_file(entity, files)
        return [message.id for message in messages]
    if kind in BYTE_FIXTURES:
        fixture = BYTE_FIXTURES[kind]
        path = materialize_fixture(kind, fixture_dir)
        message = await tg.send_file(
            entity,
            path,
            attributes=fixture.attributes(),
            force_document=fixture.force_document,
            mime_type=fixture.mime_type,
        )
        return [message.id]
    media = non_byte_media()[kind]
    update = await tg(
        functions.messages.SendMediaRequest(
            peer=entity, media=media, message="", random_id=_random_id()
        )
    )
    return [_sent_message_id(update)]


async def seed_sources(tg, manifest, manifest_path, account_alias, note) -> dict:
    results: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="tgcli-r1-fixtures-") as fixture_dir:
        fixture_path = Path(fixture_dir)
        for role in ("protected_source", "open_source"):
            entity = await _lab_entity(tg, manifest, role)
            results[role] = {}
            for kind in pending_kinds(manifest, role):
                try:
                    ids = await _audited_mutation(
                        "mirror-lab-seed",
                        account_alias,
                        {"role": role, "kind": kind},
                        lambda: _seed_kind(tg, entity, kind, fixture_path),
                    )
                except FloodWaitError:
                    raise
                except Exception as exc:
                    results[role][kind] = f"blocked:{type(exc).__name__}"
                    record_blocked(manifest, role, kind, type(exc).__name__)
                    save_manifest(manifest_path, manifest)
                    note(f"{role}: {kind} blocked by {type(exc).__name__}")
                    continue
                record_seed(manifest, role, kind, ids)
                save_manifest(manifest_path, manifest)
                results[role][kind] = "seeded"
                note(f"{role}: seeded {kind}")
    return results


# --- Task 5: copy transports ---

import shutil
from pathlib import Path

from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChatForwardsRestrictedError,
    FloodWaitError,
)


async def copy_native(tg, manifest, account_alias, note) -> dict:
    open_entity = await _lab_entity(tg, manifest, "open_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_native")
    protected_entity = await _lab_entity(tg, manifest, "protected_source")

    protected_seeds = seeded_ids(manifest, "protected_source")
    restricted_check = "skipped:no_protected_seed"
    if protected_seeds:
        first_ids = next(iter(protected_seeds.values()))
        try:
            await _audited_mutation(
                "mirror-lab-forward-restricted-check",
                account_alias,
                {"ids": len(first_ids)},
                lambda: tg(
                    functions.messages.ForwardMessagesRequest(
                        from_peer=protected_entity,
                        id=list(first_ids),
                        random_id=[_random_id() for _ in first_ids],
                        to_peer=dest_entity,
                        drop_author=True,
                    )
                ),
            )
            restricted_check = "unexpected_success"
        except ChatForwardsRestrictedError:
            restricted_check = "confirmed"
        note(f"protected forward check: {restricted_check}")

    results: dict[str, str] = {}
    for kind, ids in seeded_ids(manifest, "open_source").items():
        try:
            await _audited_mutation(
                "mirror-lab-copy-native",
                account_alias,
                {"kind": kind, "ids": len(ids)},
                lambda: tg(
                    functions.messages.ForwardMessagesRequest(
                        from_peer=open_entity,
                        id=list(ids),
                        random_id=[_random_id() for _ in ids],
                        to_peer=dest_entity,
                        drop_author=True,
                    )
                ),
            )
        except FloodWaitError:
            raise
        except Exception as exc:
            results[kind] = f"blocked:{type(exc).__name__}"
            note(f"native {kind}: blocked by {type(exc).__name__}")
            continue
        results[kind] = "forwarded"
        note(f"native {kind}: forwarded")
    return {
        "transport": "native",
        "restricted_check": restricted_check,
        "results": results,
    }


async def copy_reupload(tg, manifest, workdir: Path, account_alias, note) -> dict:
    source_entity = await _lab_entity(tg, manifest, "protected_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_reupload")
    enforce_mutation_allowed(readonly=False)

    results: dict[str, str] = {}
    run_dir = Path(workdir) / "reupload"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        for kind, ids in seeded_ids(manifest, "protected_source").items():
            if kind not in BYTE_FIXTURES and kind != "album":
                results[kind] = "not_applicable"
                continue
            try:
                async def reupload_one():
                    messages = await tg.get_messages(source_entity, ids=list(ids))
                    paths = []
                    attributes = None
                    force_document = False
                    for message in messages:
                        target = run_dir / f"{kind}-{message.id}"
                        paths.append(Path(await tg.download_media(message, file=target)))
                    if kind in BYTE_FIXTURES:
                        document = getattr(messages[0], "document", None)
                        if document is not None:
                            attributes = list(document.attributes)
                            force_document = BYTE_FIXTURES[kind].force_document
                    if kind == "album":
                        return await tg.send_file(dest_entity, [str(p) for p in paths])
                    return await tg.send_file(
                        dest_entity,
                        str(paths[0]),
                        attributes=attributes,
                        force_document=force_document,
                    )

                await _audited_mutation(
                    "mirror-lab-copy-reupload",
                    account_alias,
                    {"kind": kind, "ids": len(ids)},
                    reupload_one,
                )
            except FloodWaitError:
                raise
            except Exception as exc:
                results[kind] = f"blocked:{type(exc).__name__}"
                note(f"reupload {kind}: blocked by {type(exc).__name__}")
                continue
            results[kind] = "copied"
            note(f"reupload {kind}: copied")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return {"transport": "reupload", "results": results}


# --- Task 6: teardown ---

async def teardown_lab(tg, manifest, manifest_path, account_alias, note) -> dict:
    removed = []
    unresolved = []
    for role, channel in list(manifest["channels"].items()):
        deleting = channel.get("delete_state") == "deleting"
        try:
            entity = await _lab_entity(tg, manifest, role)
        except (ChannelInvalidError, ChannelPrivateError):
            if not deleting:
                raise
            unresolved.append(role)
            note(f"{role}: delete remains ambiguous; manifest entry preserved")
            continue
        if not deleting:
            channel["delete_state"] = "deleting"
            save_manifest(manifest_path, manifest)
        if entity is not None:
            try:
                await _audited_mutation(
                    "mirror-lab-teardown",
                    account_alias,
                    {"role": role},
                    lambda: tg(
                        functions.channels.DeleteChannelRequest(channel=entity)
                    ),
                )
            except (ChannelInvalidError, ChannelPrivateError):
                unresolved.append(role)
                note(f"{role}: delete remains ambiguous; manifest entry preserved")
                continue
        removed.append(role)
        del manifest["channels"][role]
        save_manifest(manifest_path, manifest)
        note(f"{role}: deleted lab channel")
    return {"removed": removed, "unresolved": unresolved}
