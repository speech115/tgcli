"""Classify which Telegram media a clone can carry natively."""

from telethon.tl import types


_NATIVE_MEDIA_TYPES = (
    types.MessageMediaWebPage,
    types.MessageMediaPhoto,
    types.MessageMediaDocument,
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
    return (
        None
        if (
            media is None or isinstance(media, _NATIVE_MEDIA_TYPES) or supports(message)
        )
        else type(media).__name__
    )
