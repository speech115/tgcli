"""Peer discovery: `tg resolve REF` (contacts.resolvePhone / entity lookup)."""

from telethon.tl import functions, types

from tgcli import chatref
from tgcli.errors import NotFoundError


def _is_phone(ref: str) -> bool:
    stripped = ref.lstrip()
    return stripped.startswith("+") and stripped[1:].isdigit()


def peer_to_dict(entity) -> dict:
    """Project a Telethon user/chat/channel entity into the `peer` JSON shape.

    Reused by `commands.contacts` (later task) so keep this free of any
    resolve-specific concerns.
    """
    is_bot = bool(getattr(entity, "bot", False))
    if is_bot:
        entity_type = "bot"
    elif getattr(entity, "broadcast", False):
        entity_type = "channel"
    elif getattr(entity, "megagroup", False) or getattr(entity, "title", None):
        entity_type = "group"
    else:
        entity_type = "user"

    display_name = getattr(entity, "title", None)
    if not display_name:
        display_name = (
            " ".join(
                part
                for part in (
                    getattr(entity, "first_name", None),
                    getattr(entity, "last_name", None),
                )
                if part
            )
            or None
        )

    return {
        "id": entity.id,
        "type": entity_type,
        "username": getattr(entity, "username", None),
        "display_name": display_name,
        "is_contact": bool(getattr(entity, "contact", False)),
        "is_bot": is_bot,
    }


def _entity_from_resolved_peer(response):
    peer = response.peer
    if isinstance(peer, types.PeerUser):
        wanted_id, pool = peer.user_id, response.users
    elif isinstance(peer, types.PeerChat):
        wanted_id, pool = peer.chat_id, response.chats
    elif isinstance(peer, types.PeerChannel):
        wanted_id, pool = peer.channel_id, response.chats
    else:
        return None
    return next((item for item in pool if item.id == wanted_id), None)


async def resolve(tg, ref: str) -> dict:
    if _is_phone(ref):
        phone = ref.lstrip()[1:]
        response = await tg(functions.contacts.ResolvePhoneRequest(phone=phone))
        entity = _entity_from_resolved_peer(response)
        if entity is None:
            raise NotFoundError(f"phone not found: {ref!r}")
        return {"peer": peer_to_dict(entity)}
    try:
        entity = await tg.get_entity(chatref.parse(ref))
    except ValueError:
        raise NotFoundError(f"dialog not found: {ref!r}") from None
    return {"peer": peer_to_dict(entity)}


def to_rows(data: dict) -> list[tuple]:
    peer = data["peer"]
    return [(peer["id"], peer["type"], peer["username"], peer["display_name"])]
