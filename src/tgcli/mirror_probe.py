from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import telethon
from telethon.tl import alltlobjects, types

from tgcli import chatref


NON_BYTE_KINDS = {
    "text", "webpage", "poll", "todo", "contact", "geo", "geo_live",
    "venue", "dice", "giveaway", "giveaway_results", "invoice", "game",
    "paid_media_preview", "paid_media_revealed", "service", "story", "empty",
    "unsupported",
}


def _document_kind(document) -> str:
    attributes = list(getattr(document, "attributes", ()) or ())
    if any(isinstance(attribute, types.DocumentAttributeSticker) for attribute in attributes):
        return "sticker"
    videos = [
        attribute
        for attribute in attributes
        if isinstance(attribute, types.DocumentAttributeVideo)
    ]
    if any(attribute.round_message for attribute in videos):
        return "video_note"
    if any(isinstance(attribute, types.DocumentAttributeAnimated) for attribute in attributes):
        return "animation"
    for attribute in attributes:
        if isinstance(attribute, types.DocumentAttributeAudio):
            return "voice" if attribute.voice else "audio"
    if videos:
        return "video"
    return "document"


def classify_message(message) -> str:
    if getattr(message, "action", None) is not None:
        return "service"
    media = getattr(message, "media", None)
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return "text" if getattr(message, "message", "") else "empty"
    if isinstance(media, types.MessageMediaPhoto):
        return "photo"
    if isinstance(media, types.MessageMediaDocument):
        return _document_kind(media.document)
    mapping = {
        types.MessageMediaWebPage: "webpage",
        types.MessageMediaPoll: "poll",
        types.MessageMediaToDo: "todo",
        types.MessageMediaContact: "contact",
        types.MessageMediaGeo: "geo",
        types.MessageMediaGeoLive: "geo_live",
        types.MessageMediaVenue: "venue",
        types.MessageMediaDice: "dice",
        types.MessageMediaGiveaway: "giveaway",
        types.MessageMediaGiveawayResults: "giveaway_results",
        types.MessageMediaInvoice: "invoice",
        types.MessageMediaGame: "game",
        types.MessageMediaStory: "story",
        types.MessageMediaUnsupported: "unsupported",
    }
    if isinstance(media, types.MessageMediaPaidMedia):
        revealed = bool(media.extended_media) and all(
            type(item).__name__ == "MessageExtendedMedia"
            for item in media.extended_media
        )
        return "paid_media_revealed" if revealed else "paid_media_preview"
    for media_type, kind in mapping.items():
        if isinstance(media, media_type):
            return kind
    return "unsupported"


def empty_capability(kind: str, sample_id: int) -> dict:
    return {
        "kind": kind,
        "sample_message_id": sample_id,
        "decode": "pass",
        "telethon_bytes": "not_applicable" if kind in NON_BYTE_KINDS else "not_tested",
        "bytes": None,
        "sha256": None,
        "error": None,
    }


async def probe_message(tg, message) -> dict:
    kind = classify_message(message)
    result = empty_capability(kind, message.id)
    if result["telethon_bytes"] == "not_applicable":
        if kind == "story":
            result["decode"] = "unsupported"
            result["error"] = "stories_excluded"
        elif kind == "unsupported":
            result["decode"] = "unsupported"
            result["error"] = "unsupported_media"
        return result

    digest = hashlib.sha256()
    byte_count = 0
    try:
        async for chunk in tg.iter_download(message.media, request_size=512 * 1024):
            data = bytes(chunk)
            digest.update(data)
            byte_count += len(data)
    except Exception as exc:
        result.update(
            telethon_bytes="inconclusive",
            bytes=byte_count,
            sha256=None,
            error=type(exc).__name__,
        )
        return result

    if byte_count == 0:
        result.update(telethon_bytes="fail", bytes=0, error="zero_bytes")
        return result
    result.update(
        telethon_bytes="pass",
        bytes=byte_count,
        sha256=digest.hexdigest(),
    )
    return result


def runtime_metadata() -> dict:
    return {
        "telethon": telethon.__version__,
        "telegram_layer": alltlobjects.LAYER,
        "probed_at": datetime.now(UTC).isoformat(),
    }


def _source_fingerprint(account_user_id: int, peer_id: int) -> str:
    value = f"{account_user_id}:{peer_id}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def _aggregate_kind(kind: str, rows: list[dict], target: int) -> dict:
    states = {row["telethon_bytes"] for row in rows}
    telethon_bytes = next(iter(states)) if len(states) == 1 else "inconclusive"
    return {
        "kind": kind,
        "sample_count": len(rows),
        "coverage": "complete" if len(rows) >= target else "limited",
        "telethon_bytes": telethon_bytes,
        "samples": rows,
    }


async def probe_chat(
    tg,
    chat: str,
    account_user_id: int,
    *,
    role: str,
    limit: int,
    samples_per_kind: int = 3,
) -> dict:
    if role not in {"owned", "subscriber", "lab"}:
        raise ValueError(f"invalid probe role: {role}")
    if not 1 <= samples_per_kind <= 3:
        raise ValueError("samples_per_kind must be between 1 and 3")

    entity = await tg.get_entity(chatref.parse(chat))
    samples: dict[str, list[dict]] = {}
    protected = bool(getattr(entity, "noforwards", False))
    scanned = 0
    async for message in tg.iter_messages(entity, limit=limit):
        scanned += 1
        protected = protected or bool(getattr(message, "noforwards", False))
        kind = classify_message(message)
        rows = samples.setdefault(kind, [])
        if len(rows) < samples_per_kind:
            rows.append(await probe_message(tg, message))

    return {
        "probe_version": 1,
        "runtime": runtime_metadata(),
        "source": {
            "fingerprint": _source_fingerprint(account_user_id, entity.id),
            "protected": protected,
            "role": role,
            "scanned": scanned,
        },
        "capabilities": [
            _aggregate_kind(kind, samples[kind], samples_per_kind)
            for kind in sorted(samples)
        ],
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
