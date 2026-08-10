"""Classify which Telegram media and chrome a clone can carry natively."""

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


def dropped_buttons(message) -> list[dict] | None:
    """The message's keyboard buttons, or None when it carries no keyboard.

    Telegram binds a keyboard to the bot that attached it, so no copy this
    tool can make will carry one: a user account cannot send `reply_markup`,
    and `InputSingleMedia` has no markup field at all, which puts an album
    out of reach even in principle. Only a native forward keeps the rows, and
    that path is closed to a protected source. The buttons are reported so the
    loss is visible, and never reconstructed — a rebuilt callback row would
    claim a bot's behaviour the clone cannot honour (ADR-0085).
    """
    rows = getattr(getattr(message, "reply_markup", None), "rows", None) or ()
    buttons = [
        {"type": type(button).__name__, "text": getattr(button, "text", None)}
        for row in rows
        for button in getattr(row, "buttons", None) or ()
    ]
    return buttons or None


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
