"""Inbox dialog state mutations (ADR-0029): pin / unpin."""

from telethon.tl import functions, types

from tgcli import chatref
from tgcli.errors import NotFoundError


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def set_pinned(tg, chat: str, pinned: bool) -> dict:
    entity = await _entity(tg, chat)
    input_peer = await tg.get_input_entity(chatref.parse(chat))
    await tg(
        functions.messages.ToggleDialogPinRequest(
            peer=types.InputDialogPeer(peer=input_peer),
            pinned=pinned,
        )
    )
    return {"dialog": {"id": entity.id}, "pinned": pinned}


def to_rows(data: dict) -> list[tuple]:
    return [(data["dialog"]["id"], "pinned" if data["pinned"] else "unpinned")]
