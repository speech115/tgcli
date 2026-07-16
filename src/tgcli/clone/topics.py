"""Forum destinations and topic maps for forum clones (ADR-0022)."""

from telethon.tl import functions

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
