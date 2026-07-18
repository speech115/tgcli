"""Fail-closed message-id confirmation for random_id-based sends (ADR-0028)."""

from typing import cast

from telethon.tl import types

from tgcli.errors import PolicyError


def confirmed_ids(response, random_ids: list[int]) -> list[int]:
    if isinstance(response, types.UpdateShortSentMessage):
        message_id = response.id
        if (
            len(random_ids) == 1
            and isinstance(message_id, int)
            and not isinstance(message_id, bool)
            and message_id > 0
        ):
            return [message_id]
        raise PolicyError("Telegram did not confirm the send")
    updates = getattr(response, "updates", ())
    matches = {
        update.random_id: update.id
        for update in updates
        if isinstance(update, types.UpdateMessageID)
    }
    message_ids = [matches.get(random_id) for random_id in random_ids]
    if (
        set(matches) != set(random_ids)
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in message_ids
        )
        or len(set(message_ids)) != len(message_ids)
    ):
        raise PolicyError("Telegram did not confirm the send")
    return cast(list[int], message_ids)
