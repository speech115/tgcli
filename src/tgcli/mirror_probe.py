from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import telethon
from telethon.tl import alltlobjects, types


NON_BYTE_KINDS = {
    "text", "webpage", "poll", "todo", "contact", "geo", "geo_live",
    "venue", "dice", "giveaway", "giveaway_results", "invoice", "game",
    "paid_media_preview", "paid_media_revealed", "service", "story", "empty",
    "unsupported",
}


def _document_kind(document) -> str:
    attributes = list(getattr(document, "attributes", ()) or ())
    for attribute in attributes:
        if isinstance(attribute, types.DocumentAttributeSticker):
            return "sticker"
        if isinstance(attribute, types.DocumentAttributeAnimated):
            return "animation"
        if isinstance(attribute, types.DocumentAttributeAudio):
            return "voice" if attribute.voice else "audio"
        if isinstance(attribute, types.DocumentAttributeVideo):
            if attribute.round_message:
                return "video_note"
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


def runtime_metadata() -> dict:
    return {
        "telethon": telethon.__version__,
        "telegram_layer": alltlobjects.LAYER,
        "probed_at": datetime.now(UTC).isoformat(),
    }
