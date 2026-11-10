"""Outgoing messages: random_id, reply header, and fail-closed confirmation.

A send carries a random_id so Telegram dedupes a retry, and its message id is
taken only from Telegram's confirmation of that random_id (ADR-0028).
"""

import secrets
from typing import cast

from telethon.errors import RandomIdDuplicateError
from telethon.tl import types

from tgcli.errors import PolicyError


def random_id() -> int:
    return secrets.randbelow(2**63 - 1) + 1


def reply_header(reply_to: int | None, topic: int | None):
    """The InputReplyTo for a reply, a forum topic post, or both; None for neither."""
    if reply_to is None and topic is None:
        return None
    return types.InputReplyToMessage(
        reply_to_msg_id=cast(int, reply_to if reply_to is not None else topic),
        top_msg_id=cast(int, topic)
        if reply_to is not None and topic is not None
        else None,
    )


def confirmed_ids(
    response, random_ids: list[int], *, what: str = "the send"
) -> list[int]:
    if isinstance(response, types.UpdateShortSentMessage):
        message_id = response.id
        if (
            len(random_ids) == 1
            and isinstance(message_id, int)
            and not isinstance(message_id, bool)
            and message_id > 0
        ):
            return [message_id]
        raise PolicyError(f"Telegram did not confirm {what}")
    updates = getattr(response, "updates", ())
    confirmations = [
        (update.random_id, update.id)
        for update in updates
        if isinstance(update, types.UpdateMessageID)
    ]
    matches = dict(confirmations)
    message_ids = [matches.get(random_id) for random_id in random_ids]
    if (
        len(confirmations) != len(random_ids)
        or set(matches) != set(random_ids)
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in message_ids
        )
        or len(set(message_ids)) != len(message_ids)
    ):
        raise PolicyError(f"Telegram did not confirm {what}")
    return cast(list[int], message_ids)


async def send_confirmed(tg, request, random_id: int) -> dict:
    """Send one message request and report its confirmed id.

    RANDOM_ID_DUPLICATE means an earlier attempt with this preview's random_id
    was delivered, but Telegram does not say which message it became.
    """
    try:
        response = await tg(request)
    except RandomIdDuplicateError:
        return {"message_id": None, "already_sent": True}
    [message_id] = confirmed_ids(response, [random_id])
    return {"message_id": message_id, "already_sent": False}
