"""Forum destinations and topic maps for forum clones (ADR-0022)."""

import secrets

from telethon.tl import functions, types

from tgcli.clone import state
from tgcli.errors import PolicyError

GENERAL_TOPIC_ID = 1


def is_forum_destination(entity, *, title: str | None = None) -> bool:
    active = any(getattr(item, "active", False)
                 for item in (getattr(entity, "usernames", None) or ()))
    return bool((title is None or getattr(entity, "title", None) == title)
                and getattr(entity, "creator", False)
                and getattr(entity, "megagroup", False)
                and not getattr(entity, "broadcast", False)
                and getattr(entity, "username", None) is None and not active)


def create_request(marker: str):
    return functions.channels.CreateChannelRequest(
        title=marker, about="", broadcast=False, megagroup=True)


async def ensure_forum(mutate, destination) -> None:
    if not getattr(destination, "forum", False):
        await mutate(functions.channels.ToggleForumRequest(
            channel=destination, enabled=True, tabs=False))
        destination.forum = True


def confirmed_destination_ids(response, random_ids: list[int]) -> list[int]:
    if isinstance(response, types.UpdateShortSentMessage):
        destination_id = response.id
        if (len(random_ids) == 1 and isinstance(destination_id, int)
                and not isinstance(destination_id, bool) and destination_id > 0):
            return [destination_id]
        raise PolicyError("Telegram did not confirm the complete cloned batch")
    updates = getattr(response, "updates", ())
    confirmations = [(update.random_id, update.id) for update in updates
                     if isinstance(update, types.UpdateMessageID)]
    matches = dict(confirmations)
    destination_ids = [matches.get(random_id) for random_id in random_ids]
    if (len(confirmations) != len(random_ids) or set(matches) != set(random_ids)
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0
                   for item in destination_ids)
            or len(set(destination_ids)) != len(destination_ids)):
        raise PolicyError("Telegram did not confirm the complete cloned batch")
    return destination_ids


async def create_topic(mutate, destination, clone_state, source_topic_id, *,
                       title: str, icon_color=None, icon_emoji_id=None) -> int:
    random_id = secrets.randbelow(2**63 - 1) + 1
    response = await mutate(functions.messages.CreateForumTopicRequest(
        peer=destination, title=title, random_id=random_id,
        icon_color=icon_color, icon_emoji_id=icon_emoji_id))
    [destination_topic_id] = confirmed_destination_ids(response, [random_id])
    clone_state.record_topic(source_topic_id, destination_topic_id)
    state.save(clone_state)
    return destination_topic_id
