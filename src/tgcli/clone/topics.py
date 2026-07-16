"""Forum destinations and topic maps for forum clones (ADR-0022)."""
import secrets
from telethon.tl import functions, types
from tgcli import safety
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
                       account_alias: str, title: str, icon_color=None,
                       icon_emoji_id=None) -> int:
    random_id = secrets.randbelow(2**63 - 1) + 1
    request = functions.messages.CreateForumTopicRequest(
        peer=destination, title=title, random_id=random_id,
        icon_color=icon_color, icon_emoji_id=icon_emoji_id)
    safety.append_audit("clone-sync-topic", account_alias, {
        "clone_id": clone_state.clone_id, "source_topic_id": source_topic_id,
    })
    response = await mutate(request)
    [destination_topic_id] = confirmed_destination_ids(response, [random_id])
    clone_state.record_topic(source_topic_id, destination_topic_id)
    state.save(clone_state)
    return destination_topic_id


def topic_id_of(message) -> int:
    header = getattr(message, "reply_to", None)
    if header is None or not getattr(header, "forum_topic", False):
        return GENERAL_TOPIC_ID
    topic_id = header.reply_to_top_id
    topic_id = header.reply_to_msg_id if topic_id is None else topic_id
    if type(topic_id) is not int or topic_id <= 0:
        raise PolicyError("clone topic id is invalid")
    return topic_id

def placement_only(header) -> bool:
    return bool(header is not None and getattr(header, "forum_topic", False)
                and header.reply_to_top_id is None)


async def ensure_topic(mutate, source, destination, clone_state,
                       source_topic_id: int, counters: dict, *,
                       account_alias: str) -> int:
    if source_topic_id == GENERAL_TOPIC_ID:
        return GENERAL_TOPIC_ID
    known = clone_state.topic_dest_for(source_topic_id)
    if known is not None:
        return known
    response = await mutate(functions.messages.GetForumTopicsByIDRequest(
        peer=source, topics=[source_topic_id]))
    found = [item for item in getattr(response, "topics", ())
             if getattr(item, "id", None) == source_topic_id
             and isinstance(getattr(item, "title", None), str)]
    title = found[0].title if found else f"topic {source_topic_id}"
    counters["topics_created"] += 1
    return await create_topic(mutate, destination, clone_state, source_topic_id,
                              account_alias=account_alias, title=title)


def place(reply_to, destination_topic_id: int):
    if destination_topic_id == GENERAL_TOPIC_ID:
        return reply_to
    if reply_to is None:
        return types.InputReplyToMessage(reply_to_msg_id=destination_topic_id)
    reply_to.top_msg_id = destination_topic_id
    return reply_to
