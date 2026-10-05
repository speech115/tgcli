"""Mute and 'Clone' folder ergonomics for tool-created peers (ADR-0046)."""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl import functions, types

from tgcli.output import note

# Telegram's "muted forever" sentinel: the largest signed 32-bit timestamp.
MUTE_FOREVER_UNTIL = 2**31 - 1

FOLDER_TITLE = "Clone"
_MAX_FILTERS = 10
_MAX_INCLUDE_PEERS = 100
_FILTER_ID_MIN = 2
_FILTER_ID_MAX = 255


def _title_text(title) -> str:
    if isinstance(title, types.TextWithEntities):
        return title.text
    return str(title or "")


def _is_muted_forever(settings) -> bool:
    mute_until = getattr(settings, "mute_until", None)
    if mute_until is None:
        return False
    if isinstance(mute_until, datetime):
        if mute_until.tzinfo is None:
            mute_until = mute_until.replace(tzinfo=UTC)
        return mute_until.timestamp() >= MUTE_FOREVER_UNTIL - 1
    try:
        return int(mute_until) >= MUTE_FOREVER_UNTIL - 1
    except (TypeError, ValueError):
        return False


def _peer_key(peer) -> tuple | None:
    if isinstance(peer, types.InputPeerChannel):
        return ("channel", peer.channel_id)
    if isinstance(peer, types.InputPeerUser):
        return ("user", peer.user_id)
    if isinstance(peer, types.InputPeerChat):
        return ("chat", peer.chat_id)
    if isinstance(peer, types.PeerChannel):
        return ("channel", peer.channel_id)
    if isinstance(peer, types.PeerUser):
        return ("user", peer.user_id)
    if isinstance(peer, types.PeerChat):
        return ("chat", peer.chat_id)
    return None


async def _mute_peer(tg, entity) -> bool:
    input_peer = await tg.get_input_entity(entity)
    notify_peer = types.InputNotifyPeer(peer=input_peer)
    try:
        settings = await tg(
            functions.account.GetNotifySettingsRequest(peer=notify_peer)
        )
        if _is_muted_forever(settings):
            return True
        await tg(
            functions.account.UpdateNotifySettingsRequest(
                peer=notify_peer,
                # Telethon stubs type mute_until as datetime|None;
                # MTProto wants a unix int.
                settings=types.InputPeerNotifySettings(mute_until=MUTE_FOREVER_UNTIL),  # type: ignore[arg-type]
            )
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail init
        note(f"warning: clone mute failed for peer {getattr(entity, 'id', '?')}: {exc}")
        return False


async def _folder_filters(tg) -> list:
    result = await tg(functions.messages.GetDialogFiltersRequest())
    filters = getattr(result, "filters", result)
    if not isinstance(filters, list):
        return []
    return filters


def _lowest_free_id(filters: list) -> int | None:
    used = {
        item.id
        for item in filters
        if isinstance(item, (types.DialogFilter, types.DialogFilterChatlist))
    }
    for candidate in range(_FILTER_ID_MIN, _FILTER_ID_MAX + 1):
        if candidate not in used:
            return candidate
    return None


def _named_clone_filter(filters: list) -> types.DialogFilter | None:
    for item in filters:
        if (
            isinstance(item, types.DialogFilter)
            and _title_text(item.title) == FOLDER_TITLE
        ):
            return item
    return None


async def _ensure_folder(tg, entities: list) -> str:
    if not entities:
        return "present"
    try:
        input_peers = [await tg.get_input_entity(entity) for entity in entities]
        filters = await _folder_filters(tg)
        existing = _named_clone_filter(filters)
        if existing is None:
            named = [
                item
                for item in filters
                if isinstance(item, (types.DialogFilter, types.DialogFilterChatlist))
            ]
            if len(named) >= _MAX_FILTERS:
                note("warning: clone folder unavailable: Telegram folder limit reached")
                return "unavailable"
            filter_id = _lowest_free_id(filters)
            if filter_id is None:
                note("warning: clone folder unavailable: no free filter id")
                return "unavailable"
            if len(input_peers) > _MAX_INCLUDE_PEERS:
                note("warning: clone folder unavailable: include_peers limit")
                return "unavailable"
            await tg(
                functions.messages.UpdateDialogFilterRequest(
                    id=filter_id,
                    filter=types.DialogFilter(
                        id=filter_id,
                        title=types.TextWithEntities(text=FOLDER_TITLE, entities=[]),
                        pinned_peers=[],
                        include_peers=list(input_peers),
                        exclude_peers=[],
                    ),
                )
            )
            return "added"

        present = {_peer_key(peer) for peer in existing.include_peers}
        missing = [peer for peer in input_peers if _peer_key(peer) not in present]
        if not missing:
            return "present"
        combined = list(existing.include_peers) + missing
        if len(combined) > _MAX_INCLUDE_PEERS:
            note("warning: clone folder unavailable: include_peers limit")
            return "unavailable"
        await tg(
            functions.messages.UpdateDialogFilterRequest(
                id=existing.id,
                filter=types.DialogFilter(
                    id=existing.id,
                    title=existing.title,
                    pinned_peers=list(existing.pinned_peers),
                    include_peers=combined,
                    exclude_peers=list(existing.exclude_peers),
                    contacts=existing.contacts,
                    non_contacts=existing.non_contacts,
                    groups=existing.groups,
                    broadcasts=existing.broadcasts,
                    bots=existing.bots,
                    exclude_muted=existing.exclude_muted,
                    exclude_read=existing.exclude_read,
                    exclude_archived=existing.exclude_archived,
                    title_noanimate=existing.title_noanimate,
                    emoticon=existing.emoticon,
                    color=existing.color,
                ),
            )
        )
        return "added"
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail init
        note(f"warning: clone folder unavailable: {exc}")
        return "unavailable"


async def apply(tg, peers: list) -> dict:
    """Mute tool-created peers and file them into the Clone folder.

    Returns ``{"muted": bool, "folder": "added"|"present"|"unavailable"}``.
    Failures warn on stderr and never raise.
    """
    entities = [peer for peer in peers if peer is not None]
    muted_ok = True
    for entity in entities:
        if not await _mute_peer(tg, entity):
            muted_ok = False
    folder = await _ensure_folder(tg, entities)
    return {"muted": muted_ok, "folder": folder}
