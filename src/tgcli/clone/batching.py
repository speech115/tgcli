"""Pure batch planning for clone sync: albums and service skips, no Telethon."""

from dataclasses import dataclass
from typing import Any

from tgcli.errors import PolicyError


@dataclass(frozen=True)
class ServiceSkip:
    message_id: int
    message: Any


@dataclass(frozen=True)
class Batch:
    messages: tuple


def _group_id(message):
    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id is None:
        return None
    if isinstance(grouped_id, bool) or not isinstance(grouped_id, int):
        raise PolicyError("clone album group id is invalid")
    return grouped_id


async def plan(messages):
    """Yield ServiceSkip and Batch events from an async message stream."""
    album: list = []
    async for message in messages:
        if getattr(message, "action", None) is not None:
            if album:
                yield Batch(tuple(album))
                album = []
            yield ServiceSkip(message.id, message)
            continue
        grouped_id = _group_id(message)
        if grouped_id is not None:
            if album and album[0].grouped_id == grouped_id:
                album.append(message)
                continue
            if album:
                yield Batch(tuple(album))
            album = [message]
            continue
        if album:
            yield Batch(tuple(album))
            album = []
        yield Batch((message,))
    if album:
        yield Batch(tuple(album))
