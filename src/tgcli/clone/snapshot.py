"""Render truthful text snapshots for polls and stories (ADR-0019)."""

from telethon.tl import types


def _vote_word(value: int) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return "голос"
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return "голоса"
    return "голосов"


def _bar(percent: int, width: int = 10) -> str:
    eighths = round(percent * width * 8 / 100)
    full, remainder = divmod(eighths, 8)
    partial = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")[remainder]
    empty = width - full - bool(remainder)
    return "█" * full + partial + "░" * empty


def _poll_snapshot(media: types.MessageMediaPoll) -> tuple[str, list]:
    total = media.results.total_voters or 0
    counts = {
        result.option: result.voters or 0
        for result in (media.results.results or ())
    }
    options = []
    for answer in media.poll.answers:
        voters = counts.get(answer.option, 0)
        percent = round(voters * 100 / total) if total else 0
        options.append(
            f"{answer.text.text}\n{_bar(percent)} {percent}% · "
            f"{voters} {_vote_word(voters)}"
        )
    text = "\n\n".join([
        "📊 Результаты опроса", media.poll.question.text,
        *options, f"Проголосовало: {total}",
    ])
    return text, []


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


async def render(tg, message) -> tuple[str, list]:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        return _poll_snapshot(media)
    assert isinstance(media, types.MessageMediaStory), "render() requires fidelity.supports()"
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
    label = title or name or (f"@{username}" if username else "неизвестен")
    prefix = "Stories недоступна\nАвтор: "
    text = prefix + label
    entities = [types.MessageEntityTextUrl(
        offset=_utf16_length(prefix), length=_utf16_length(label),
        url=f"https://t.me/{username}",
    )] if username else []
    return text, entities
