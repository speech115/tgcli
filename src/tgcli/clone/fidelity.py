"""Truthful text fallbacks for Telegram media that cannot be cloned exactly."""

from datetime import UTC, datetime

from telethon.tl import types


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
    return None if (media is None or isinstance(media, _NATIVE_MEDIA_TYPES)
                    or supports(message)) else type(media).__name__


def _poll_snapshot(media: types.MessageMediaPoll) -> str:
    total = media.results.total_voters or 0
    counts = {
        result.option: result.voters or 0
        for result in (media.results.results or ())
    }
    options = []
    for answer in media.poll.answers:
        voters = counts.get(answer.option, 0)
        percent = round(voters * 100 / total) if total else 0
        options.append(f"- {answer.text.text} — {voters} ({percent}%)")
    mode = "multiple choice" if media.poll.multiple_choice else "single choice"
    status = "closed" if media.poll.closed else "open at clone time"
    captured = datetime.now(UTC).isoformat(timespec="seconds")
    return "\n".join([
        f"Poll snapshot ({captured})",
        media.poll.question.text,
        *options,
        f"Total voters: {total}",
        f"Source poll: {mode}; {status}.",
    ])


async def replacement_text(tg, message) -> str | None:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        return _poll_snapshot(media)
    if not isinstance(media, types.MessageMediaStory):
        return None
    try:
        peer = await tg.get_entity(media.peer)
    except ValueError:
        peer = None
    title = getattr(peer, "title", None)
    name = " ".join(
        item for item in (
            getattr(peer, "first_name", None), getattr(peer, "last_name", None)
        ) if item
    )
    username = getattr(peer, "username", None)
    label = title or name or (f"@{username}" if username else str(media.peer))
    if username and label != f"@{username}":
        label += f" (@{username})"
    return "\n".join([
        "Story unavailable at clone time.",
        f"Story author: {label}",
        f"Story ID: {media.id}",
    ])
