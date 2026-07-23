"""Inbox dialog state mutations (ADR-0029/0032): pin, archive, mute."""

from datetime import datetime, timezone

from telethon.tl import functions, types

from tgcli import chatref
from tgcli.errors import NotFoundError, PolicyError

FOLDER_ID_ARCHIVE = 1
FOLDER_ID_MAIN = 0
MUTE_FOREVER_UNTIL = 2**31 - 1


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


async def set_archived(tg, chat: str, archived: bool) -> dict:
    entity = await _entity(tg, chat)
    input_peer = await tg.get_input_entity(chatref.parse(chat))
    folder_id = FOLDER_ID_ARCHIVE if archived else FOLDER_ID_MAIN
    await tg(
        functions.folders.EditPeerFoldersRequest(
            folder_peers=[types.InputFolderPeer(peer=input_peer, folder_id=folder_id)]
        )
    )
    return {"dialog": {"id": entity.id}, "archived": archived}


def _parse_until(until: str | None, forever: bool) -> tuple[int, str | None]:
    if forever and until:
        raise PolicyError("pass only one of --until or --forever")
    if forever:
        return MUTE_FOREVER_UNTIL, None
    if until is None:
        raise PolicyError("dialog mute requires --until ISO8601 or --forever")
    try:
        parsed = datetime.fromisoformat(until)
    except ValueError as exc:
        raise PolicyError(f"invalid --until timestamp: {until!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp()), parsed.astimezone(timezone.utc).isoformat()


async def set_muted(
    tg, chat: str, *, muted: bool, until: str | None = None, forever: bool = False
) -> dict:
    entity = await _entity(tg, chat)
    input_peer = await tg.get_input_entity(chatref.parse(chat))
    if muted:
        mute_until, until_out = _parse_until(until, forever)
    else:
        mute_until, until_out = 0, None
    await tg(
        functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(peer=input_peer),
            settings=types.InputPeerNotifySettings(mute_until=mute_until),
        )
    )
    return {"dialog": {"id": entity.id}, "muted": muted, "until": until_out}


def to_rows(data: dict) -> list[tuple]:
    if "pinned" in data:
        return [(data["dialog"]["id"], "pinned" if data["pinned"] else "unpinned")]
    if "archived" in data:
        return [
            (data["dialog"]["id"], "archived" if data["archived"] else "unarchived")
        ]
    if data.get("muted"):
        label = (
            "muted-forever"
            if data.get("until") is None
            else f"muted-until:{data['until']}"
        )
        return [(data["dialog"]["id"], label)]
    return [(data["dialog"]["id"], "unmuted")]
