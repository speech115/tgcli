from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.mirror_probe import write_report

LAB_MARKER = "tgcli-r1-lab"
MANIFEST_VERSION = 1
CHANNEL_ROLES = (
    "protected_source",
    "open_source",
    "dest_native",
    "dest_reupload",
)


def new_manifest(account_user_id: int) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "account_user_id": account_user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "channels": {},
        "seeded": {},
    }


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("unsupported lab manifest")
    for key in ("account_user_id", "channels", "seeded"):
        if key not in data:
            raise ValueError(f"lab manifest missing {key!r}")
    seen_peer_ids = set()
    for role, channel in data["channels"].items():
        if role not in CHANNEL_ROLES:
            raise ValueError(f"unknown lab channel role: {role}")
        if not isinstance(channel, dict):
            raise ValueError(f"invalid lab channel entry: {role}")
        peer_id = channel.get("peer_id")
        title = channel.get("title")
        if not isinstance(peer_id, int) or peer_id <= 0:
            raise ValueError(f"invalid lab peer id: {role}")
        if not isinstance(title, str) or not title.startswith(LAB_MARKER):
            raise ValueError(f"lab channel title lacks marker: {role}")
        if peer_id in seen_peer_ids:
            raise ValueError(f"duplicate lab peer id: {peer_id}")
        seen_peer_ids.add(peer_id)
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    write_report(path, manifest)


def record_channel(manifest: dict, role: str, peer_id: int, title: str) -> None:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    if not title.startswith(LAB_MARKER):
        raise PolicyError(f"channel title lacks lab marker: {title!r}")
    manifest["channels"][role] = {"peer_id": peer_id, "title": title}


def lab_peer_ids(manifest: dict) -> set[int]:
    return {entry["peer_id"] for entry in manifest["channels"].values()}


def assert_lab_peer(manifest: dict, peer_id: int) -> None:
    if peer_id not in lab_peer_ids(manifest):
        raise PolicyError(f"peer {peer_id} is not a lab channel; refusing mutation")


def record_seed(manifest: dict, role: str, kind: str, message_ids: list[int]) -> None:
    manifest["seeded"].setdefault(role, {})[kind] = list(message_ids)


def seeded_ids(manifest: dict, role: str) -> dict[str, list[int]]:
    return dict(manifest["seeded"].get(role, {}))


# --- Task 2: fixture matrix and payload generators ---

import hashlib
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
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
    "contact", "dice", "geo", "geo_live", "poll", "venue",
)

EXCLUDED_KINDS: dict[str, str] = {
    "game": "requires a bot-owned game",
    "giveaway": "requires a Premium boost purchase with real cost",
    "giveaway_results": "exists only after a finished giveaway",
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


def materialize_fixture(kind: str, directory: Path) -> Path:
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
        command = common + [
            "-f", "lavfi", "-i", "color=c=0x3366cc:s=512x512:d=1",
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
        "geo_live": types.InputMediaGeoLive(geo_point=geo_point(), period=900),
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
    seeded = manifest["seeded"].get(role, {})
    expected = {kind for kind in seeded if kind != "album"}
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
    green = not missing and not failing
    return {
        "verdict": "green" if green else "red",
        "missing": missing,
        "failing": failing,
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


async def album_group_sizes(tg, entity, *, limit: int) -> list[int]:
    groups = Counter()
    async for message in tg.iter_messages(entity, limit=limit):
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is not None:
            groups[grouped_id] += 1
    return sorted(size for size in groups.values() if size > 1)


def compare_transport(
    source_report: dict,
    dest_report: dict,
    *,
    transport: str,
    expected_kinds: set[str] | None = None,
) -> dict:
    if transport not in {"native", "reupload"}:
        raise ValueError(f"unknown transport: {transport}")
    source = {row["kind"]: row for row in source_report["capabilities"]}
    dest = {row["kind"]: row for row in dest_report["capabilities"]}
    source_shas = _kind_shas(source_report)
    dest_shas = _kind_shas(dest_report)

    rows = []
    kinds = expected_kinds or (set(source) & set(BYTE_FIXTURES))
    for kind in sorted(kinds):
        if kind == "album":
            source_groups = source_report.get("album_group_sizes", [])
            dest_groups = dest_report.get("album_group_sizes", [])
            matched = bool(source_groups) and source_groups == dest_groups
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
            matched = bool(source_shas[kind]) and source_shas[kind] == dest_shas.get(kind)
        else:
            matched = dest[kind]["telethon_bytes"] == "pass" and len(
                dest_shas.get(kind, [])
            ) == len(source_shas[kind])
        rows.append(
            {
                "kind": kind,
                "expectation": expectation,
                "result": "pass" if matched else "fail",
            }
        )
    green = rows and all(row["result"] == "pass" for row in rows)
    return {
        "transport": transport,
        "verdict": "green" if green else "red",
        "rows": rows,
    }


# --- Task 4: channel provisioning and seeding engines ---

import os
import secrets

from telethon.tl import functions

from tgcli.safety import append_audit, enforce_mutation_allowed


async def _audited_mutation(action, account_alias, details, mutation):
    enforce_mutation_allowed(readonly=False)
    operation_id = secrets.token_hex(12)
    append_audit(
        action,
        account_alias,
        {**details, "operation_id": operation_id, "phase": "attempt"},
    )
    try:
        result = await mutation()
    except Exception as exc:
        append_audit(
            action,
            account_alias,
            {
                **details,
                "operation_id": operation_id,
                "phase": "result",
                "status": "failed",
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


async def create_lab_channels(tg, manifest, manifest_path, account_alias, note) -> dict:
    stamp = manifest["created_at"][:19].replace(":", "").replace("-", "")
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
        title = f"{LAB_MARKER} {role} {stamp}"
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
        )
        channel = update.chats[0]
        record_channel(manifest, role, channel.id, title)
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
            materialize_fixture("photo", fixture_dir / f"album-{index}")
            for index, _color in enumerate(ALBUM_COLORS)
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
                except Exception as exc:
                    results[role][kind] = f"blocked:{type(exc).__name__}"
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

from telethon.errors import ChatForwardsRestrictedError


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
    for role, channel in list(manifest["channels"].items()):
        entity = await _lab_entity(tg, manifest, role)
        await _audited_mutation(
            "mirror-lab-teardown",
            account_alias,
            {"role": role},
            lambda: tg(functions.channels.DeleteChannelRequest(channel=entity)),
        )
        removed.append(role)
        del manifest["channels"][role]
        save_manifest(manifest_path, manifest)
        note(f"{role}: deleted lab channel")
    return {"removed": removed}
