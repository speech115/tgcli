"""Render truthful text snapshots for polls and stories (ADR-0019 / ADR-0048)."""

from __future__ import annotations

import os
from typing import Any

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import safety
from tgcli.output import note

BREAKDOWN_UNAVAILABLE = "распределение по вариантам недоступно"


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


def _breakdown_available(results) -> bool:
    return bool(getattr(results, "results", None))


def _poll_snapshot(
    media: types.MessageMediaPoll, *, chosen_option: bytes | None = None
) -> tuple[str, list]:
    total = media.results.total_voters or 0
    raw_counts = {
        result.option: result.voters or 0 for result in (media.results.results or ())
    }
    if chosen_option is not None and total > 0:
        total = max(0, total - 1)
        raw_counts = {
            option: max(0, voters - (1 if option == chosen_option else 0))
            for option, voters in raw_counts.items()
        }
    heading = "📊 Результаты опроса"
    question = media.poll.question.text
    if total > 0 and not raw_counts:
        text = "\n\n".join(
            [heading, question, BREAKDOWN_UNAVAILABLE, f"Проголосовало: {total}"]
        )
        return text, []
    options = []
    for answer in media.poll.answers:
        voters = raw_counts.get(answer.option, 0)  # type: ignore  # Telethon stub union
        percent = round(voters * 100 / total) if total else 0
        options.append(
            f"{answer.text.text}\n{_bar(percent)} {percent}% · "
            f"{voters} {_vote_word(voters)}"
        )
    text = "\n\n".join([heading, question, *options, f"Проголосовало: {total}"])
    return text, []


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _vote_capture_allowed() -> bool:
    if os.environ.get("TGCLI_READONLY") == "1":
        return False
    if os.environ.get("TGCLI_NO_SEND") == "1":
        return False
    return True


def _poll_eligible_for_vote(poll) -> bool:
    if getattr(poll, "closed", None):
        return False
    if getattr(poll, "quiz", None):
        return False
    if getattr(poll, "public_voters", None):
        return False
    return True


def _results_from_updates(updates) -> types.PollResults | None:
    for item in getattr(updates, "updates", None) or ():
        if isinstance(item, types.UpdateMessagePoll):
            return item.results
    return None


class _RetractFailed(Exception):
    """Internal: retract failed with a non-FloodWait error after warning."""


async def _capture_breakdown(
    tg,
    message,
    media: types.MessageMediaPoll,
    *,
    peer,
    account_alias: str,
    invoke,
) -> tuple[types.MessageMediaPoll, bytes | None, dict[str, Any]]:
    option = bytes(media.poll.answers[0].option)  # type: ignore[arg-type]
    cast = functions.messages.SendVoteRequest(
        peer=peer, msg_id=message.id, options=[option]
    )
    safety.append_audit(
        "clone-sync-poll-vote",
        account_alias,
        {"message_id": message.id, "option": option.hex()},
    )
    updates = await invoke(tg(cast))
    retract = functions.messages.SendVoteRequest(
        peer=peer, msg_id=message.id, options=[]
    )
    safety.append_audit(
        "clone-sync-poll-retract",
        account_alias,
        {"message_id": message.id},
    )

    async def retract_vote() -> None:
        try:
            await invoke(tg(retract))
        except telethon_errors.FloodWaitError:
            raise
        except (telethon_errors.RPCError, OSError) as exc:
            note(
                f"warning: clone poll vote retract failed for message {message.id}: {exc}"
            )
            raise _RetractFailed(exc) from exc

    results = _results_from_updates(updates)
    if results is None or not _breakdown_available(results):
        retract_error: BaseException | None = None
        try:
            await retract_vote()
        except _RetractFailed as exc:
            retract_error = exc.args[0] if exc.args else exc
        note(
            "warning: clone poll vote did not reveal a breakdown for message "
            f"{message.id}"
        )
        marker: dict[str, Any] = {
            "message_id": message.id,
            "status": "capture_failed",
            "error": "poll vote did not reveal a breakdown",
        }
        if retract_error is not None:
            marker["retract_error"] = str(retract_error)
        return media, None, marker
    captured = types.MessageMediaPoll(poll=media.poll, results=results)
    try:
        await retract_vote()
    except _RetractFailed as exc:
        return (
            captured,
            option,
            {
                "message_id": message.id,
                "status": "retract_failed",
                "error": str(exc.args[0] if exc.args else exc),
            },
        )
    return captured, option, {"message_id": message.id, "status": "captured"}


async def render(
    tg,
    message,
    *,
    peer=None,
    account_alias: str | None = None,
    invoke=None,
) -> tuple[str, list, dict[str, Any] | None]:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        marker: dict[str, Any] | None = None
        chosen: bytes | None = None
        working = media
        needs_breakdown = (
            media.results.total_voters or 0
        ) > 0 and not _breakdown_available(media.results)
        if needs_breakdown:
            if (
                peer is not None
                and account_alias is not None
                and invoke is not None
                and _vote_capture_allowed()
                and _poll_eligible_for_vote(media.poll)
            ):
                working, chosen, marker = await _capture_breakdown(
                    tg,
                    message,
                    media,
                    peer=peer,
                    account_alias=account_alias,
                    invoke=invoke,
                )
            else:
                if not _vote_capture_allowed():
                    reason = "readonly"
                else:
                    reason = "ineligible"
                marker = {
                    "message_id": message.id,
                    "status": "skipped",
                    "reason": reason,
                }
        text, entities = _poll_snapshot(working, chosen_option=chosen)
        return text, entities, marker
    assert isinstance(media, types.MessageMediaStory), (
        "render() requires fidelity.supports()"
    )
    try:
        peer_entity = await tg.get_entity(media.peer)
    except (ValueError, telethon_errors.RPCError):
        # Same refusal shape as attribution._resolve: a private/deleted peer
        # is a missing author label, never a failed sync.
        peer_entity = None
    title = getattr(peer_entity, "title", None)
    name = " ".join(
        item
        for item in (
            getattr(peer_entity, "first_name", None),
            getattr(peer_entity, "last_name", None),
        )
        if item
    )
    username = getattr(peer_entity, "username", None)
    label = title or name or (f"@{username}" if username else "неизвестен")
    prefix = "Stories недоступна\nАвтор: "
    text = prefix + label
    entities = (
        [
            types.MessageEntityTextUrl(
                offset=_utf16_length(prefix),
                length=_utf16_length(label),
                url=f"https://t.me/{username}",
            )
        ]
        if username
        else []
    )
    return text, entities, None
