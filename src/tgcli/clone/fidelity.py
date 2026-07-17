"""Classify which Telegram media a clone can carry natively."""

from telethon.tl import types

from tgcli.clone.snapshot import render


_NATIVE_MEDIA_TYPES = (
    types.MessageMediaWebPage, types.MessageMediaPhoto, types.MessageMediaDocument
)


def supports(message) -> bool:
    return isinstance(
        getattr(message, "media", None),
        (types.MessageMediaPoll, types.MessageMediaStory),
    )


def unsupported_kind(message) -> str | None:
    media = getattr(message, "media", None)
    if getattr(media, "ttl_seconds", None) is not None:
        return f"{type(media).__name__}TTL"
    return None if (media is None or isinstance(media, _NATIVE_MEDIA_TYPES)
                    or supports(message)) else type(media).__name__


async def replacement(tg, message) -> tuple[str, list] | None:
    # Temporary guarded wrapper, removed in Task 5. The plan's one-line alias
    # would crash: commands/clone.py still calls this unconditionally, and
    # snapshot.render() asserts on non-poll/story media, so the pre-move
    # None path must survive until the call site is rewired.
    if not supports(message):
        return None
    return await render(tg, message)
