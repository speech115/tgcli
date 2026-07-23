"""Reply-chain discovery read (ADR-0029): ancestors always, replies opt-in."""

from tgcli import chatref
from tgcli.commands.read import _dialog_name, message_to_dict
from tgcli.errors import NotFoundError


DEPTH_CAP = 100
DEFAULT_DEPTH = 20
DEFAULT_REPLIES_LIMIT = 50
NO_THREAD_NOTE = "no cheap reply thread for this message; replies omitted"


def _has_reply_thread(message) -> bool:
    replies = getattr(message, "replies", None)
    if replies is None:
        return False
    return bool(getattr(replies, "replies", 0) or getattr(replies, "comments", False))


async def fetch_thread(
    tg,
    chat: str,
    message_id: int,
    *,
    depth: int = DEFAULT_DEPTH,
    want_replies: bool = False,
    replies_limit: int = DEFAULT_REPLIES_LIMIT,
) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    root = await tg.get_messages(entity, ids=message_id)
    if root is None:
        raise NotFoundError(f"message not found: {message_id}")

    steps = max(0, min(depth, DEPTH_CAP))
    ancestors_newest_first = []
    seen = {root.id}
    parent_id = root.reply_to_msg_id
    while parent_id is not None and len(ancestors_newest_first) < steps:
        if parent_id in seen:
            break
        parent = await tg.get_messages(entity, ids=parent_id)
        if parent is None:
            break
        seen.add(parent.id)
        ancestors_newest_first.append(parent)
        parent_id = parent.reply_to_msg_id
    ancestors = list(reversed(ancestors_newest_first))

    note = None
    replies = []
    if want_replies:
        if _has_reply_thread(root):
            fetched = await tg.get_messages(
                entity, reply_to=message_id, limit=replies_limit
            )
            replies = list(fetched or [])
        else:
            note = NO_THREAD_NOTE

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "root": message_to_dict(root, entity),
        "ancestors": [message_to_dict(item, entity) for item in ancestors],
        "replies": [message_to_dict(item, entity) for item in replies],
        "note": note,
    }


def to_rows(data: dict) -> list[tuple]:
    from tgcli.commands.read import sanitize_plain_text

    rows = []
    for message in (data["root"], *data["ancestors"], *data["replies"]):
        rows.append(
            (
                message["id"],
                message["date"],
                sanitize_plain_text(message["from"]["name"]),
                sanitize_plain_text(message["text"]),
            )
        )
    return rows
