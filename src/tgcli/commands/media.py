"""Media download command helpers (Phase 3; Telethon-only)."""

from dataclasses import dataclass
from pathlib import Path
import re

from tgcli.errors import NotFoundError, PolicyError


PRIVATE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)/?$")
PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/([1-9]\d*)/?$")


@dataclass(frozen=True)
class MediaSource:
    chat: str | None
    message_id: int
    private_channel_id: int | None


def parse_source(source: str, message_id: int | None) -> MediaSource:
    private_match = PRIVATE_LINK.fullmatch(source)
    if private_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=None,
            message_id=int(private_match.group(2)),
            private_channel_id=int(private_match.group(1)),
        )

    public_match = PUBLIC_LINK.fullmatch(source)
    if public_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=f"@{public_match.group(1)}",
            message_id=int(public_match.group(2)),
            private_channel_id=None,
        )

    if message_id is None or message_id < 1:
        raise NotFoundError(f"invalid media source: {source!r}")
    return MediaSource(chat=source, message_id=message_id, private_channel_id=None)


def safe_filename(name: str | None, message_id: int) -> str:
    candidate = Path((name or "").replace("\\", "/")).name
    candidate = "".join(char if char.isprintable() else " " for char in candidate)
    candidate = candidate.strip(" .")
    return candidate if candidate else f"media-{message_id}.bin"


def destination_for(name: str, requested: str | None) -> Path:
    path = Path(requested).expanduser() if requested else Path.home() / "Downloads" / name
    if path.exists():
        raise PolicyError(f"output path already exists: {path}")
    return path
