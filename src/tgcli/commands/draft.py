"""Message drafts: show/list reads and set/clear under preview→commit (ADR-0039)."""

from __future__ import annotations

from typing import cast

from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl import functions, types
from telethon.tl.types import MessageEntityCustomEmoji

from tgcli import chatref, formatting, safety
from tgcli.commands.read import sanitize_plain_text
from tgcli.errors import NotFoundError


def _chat_to_dict(entity) -> dict:
    name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or str(getattr(entity, "id", ""))
    )
    return {"id": entity.id, "name": name}


def _custom_emoji(message: str, entities) -> list[dict]:
    if not entities:
        return []
    surrogate = add_surrogate(message)
    output = []
    for entity in entities:
        if not isinstance(entity, MessageEntityCustomEmoji):
            continue
        glyph = del_surrogate(surrogate[entity.offset : entity.offset + entity.length])
        output.append(
            {
                "id": str(entity.document_id),
                "emoji": glyph,
                "offset": entity.offset,
                "length": entity.length,
            }
        )
    return output


def _reply_fields(draft_tl) -> tuple[int | None, int | None]:
    reply = getattr(draft_tl, "reply_to", None)
    if not isinstance(reply, types.InputReplyToMessage):
        return None, None
    return reply.reply_to_msg_id, reply.top_msg_id


def draft_to_dict(entity, draft_tl) -> dict:
    """Project a TL draft into the ADR-0039 JSON object (not a message)."""
    chat = _chat_to_dict(entity)
    if not isinstance(draft_tl, types.DraftMessage):
        return {
            "chat": chat,
            "text": "",
            "custom_emoji": [],
            "reply_to_msg_id": None,
            "topic_id": None,
            "date": None,
            "is_empty": True,
        }
    text = draft_tl.message or ""
    reply_to_msg_id, topic_id = _reply_fields(draft_tl)
    date = draft_tl.date
    return {
        "chat": chat,
        "text": text,
        "custom_emoji": _custom_emoji(text, draft_tl.entities),
        "reply_to_msg_id": reply_to_msg_id,
        "topic_id": topic_id,
        "date": date.isoformat() if date is not None else None,
        "is_empty": not text,
    }


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def fetch_show(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    peer = await tg.get_input_entity(chatref.parse(chat))
    result = await tg(
        functions.messages.GetPeerDialogsRequest(
            peers=[types.InputDialogPeer(peer=peer)]
        )
    )
    draft_tl = result.dialogs[0].draft if result.dialogs else None
    return {"draft": draft_to_dict(entity, draft_tl)}


async def fetch_list(tg) -> dict:
    result = await tg(functions.messages.GetAllDraftsRequest())
    users = {user.id: user for user in result.users}
    chats = {chat.id: chat for chat in result.chats}
    drafts = []
    for update in result.updates:
        peer = update.peer
        if isinstance(peer, types.PeerUser):
            entity = users.get(peer.user_id)
        elif isinstance(peer, types.PeerChat):
            entity = chats.get(peer.chat_id)
        elif isinstance(peer, types.PeerChannel):
            entity = chats.get(peer.channel_id)
        else:
            entity = None
        if entity is None:
            continue
        drafts.append(draft_to_dict(entity, update.draft))
    return {"drafts": drafts}


def show_to_rows(data: dict) -> list[tuple]:
    draft = data["draft"]
    return [
        (
            draft["chat"]["id"],
            draft["chat"]["name"],
            sanitize_plain_text(draft["text"]),
            draft["is_empty"],
        )
    ]


def list_to_rows(data: dict) -> list[tuple]:
    return [
        (
            draft["chat"]["id"],
            draft["chat"]["name"],
            sanitize_plain_text(draft["text"]),
            draft["is_empty"],
        )
        for draft in data["drafts"]
    ]


def _reply_header(payload: dict):
    reply_to, topic = payload.get("reply_to"), payload.get("topic")
    if reply_to is None and topic is None:
        return None
    return types.InputReplyToMessage(
        reply_to_msg_id=cast(int, reply_to if reply_to is not None else topic),
        top_msg_id=cast(int, topic)
        if reply_to is not None and topic is not None
        else None,
    )


async def prepare_set(
    tg,
    chat: str,
    text: str,
    *,
    fmt: str = "md",
    reply_to: int | None = None,
    topic: int | None = None,
) -> dict:
    formatting.render(text, fmt)
    entity = await _entity(tg, chat)
    current = await fetch_show(tg, chat)
    stored = safety.create_preview(
        {
            "kind": "draft-set",
            "chat": chat,
            "old_text": current["draft"]["text"],
            "text": text,
            "format": fmt,
            "reply_to": reply_to,
            "topic": topic,
            "to": _chat_to_dict(entity),
        }
    )
    keys = (
        "preview_id",
        "to",
        "old_text",
        "text",
        "format",
        "reply_to",
        "topic",
        "expires_at",
    )
    return {key: stored[key] for key in keys}


async def prepare_clear(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    current = await fetch_show(tg, chat)
    stored = safety.create_preview(
        {
            "kind": "draft-clear",
            "chat": chat,
            "old_text": current["draft"]["text"],
            "to": _chat_to_dict(entity),
        }
    )
    keys = ("preview_id", "to", "old_text", "expires_at")
    return {key: stored[key] for key in keys}


async def _save_draft(tg, payload: dict, *, message: str, entities) -> None:
    peer = await tg.get_input_entity(chatref.parse(payload["chat"]))
    await tg(
        functions.messages.SaveDraftRequest(
            peer=peer,
            message=message,
            entities=entities,
            reply_to=_reply_header(payload),
        )
    )


async def commit_set(tg, preview_id: str, payload: dict) -> dict:
    body, entities = formatting.render(payload["text"], payload.get("format", "md"))
    await _save_draft(tg, payload, message=body, entities=entities)
    entity = await _entity(tg, payload["chat"])
    draft_tl = types.DraftMessage(
        message=body,
        date=None,
        entities=entities,
        reply_to=_reply_header(payload),
    )
    return {"preview_id": preview_id, "draft": draft_to_dict(entity, draft_tl)}


async def commit_clear(tg, preview_id: str, payload: dict) -> dict:
    await _save_draft(tg, payload, message="", entities=None)
    entity = await _entity(tg, payload["chat"])
    return {
        "preview_id": preview_id,
        "draft": draft_to_dict(entity, types.DraftMessageEmpty()),
    }


def mutation_to_rows(data: dict) -> list[tuple]:
    if "old_text" in data:
        return [
            (
                data["preview_id"],
                data["to"]["id"],
                sanitize_plain_text(data["old_text"]),
                sanitize_plain_text(data.get("text", "")),
                data.get("format", ""),
            )
        ]
    draft = data["draft"]
    return [
        (
            data["preview_id"],
            draft["chat"]["id"],
            sanitize_plain_text(draft["text"]),
            draft["is_empty"],
        )
    ]
