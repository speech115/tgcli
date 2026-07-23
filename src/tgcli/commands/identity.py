"""Peer discovery: `tg resolve REF` (contacts.resolvePhone / entity lookup)."""

from telethon.tl import functions, types

from tgcli import chatref
from tgcli.commands.read import sanitize_plain_text
from tgcli.errors import NotFoundError, PolicyError


def _is_phone(stripped_ref: str) -> bool:
    """`stripped_ref` must already be `ref.lstrip()`d by the caller."""
    return stripped_ref.startswith("+") and stripped_ref[1:].isdigit()


def peer_to_dict(entity) -> dict:
    """Project a Telethon user/chat/channel entity into the `peer` JSON shape.

    Reused by ``contacts_list`` / ``contacts_search`` in this module.
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
    stripped = ref.lstrip()
    if _is_phone(stripped):
        from tgcli.resolve_phone import enforce_resolve_phone_cooldown

        enforce_resolve_phone_cooldown()
        phone = stripped[1:]
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
    return [
        (
            peer["id"],
            peer["type"],
            sanitize_plain_text(peer["username"]),
            sanitize_plain_text(peer["display_name"]),
        )
    ]


CONTACTS_SEARCH_GLOBAL_LIMIT = 50


async def contacts_list(tg) -> dict:
    response = await tg(functions.contacts.GetContactsRequest(hash=0))
    return {"contacts": [peer_to_dict(user) for user in response.users]}


async def contacts_search(tg, query: str, *, use_global: bool = False) -> dict:
    if use_global:
        response = await tg(
            functions.contacts.SearchRequest(
                q=query, limit=CONTACTS_SEARCH_GLOBAL_LIMIT
            )
        )
        return {
            "contacts": [peer_to_dict(user) for user in response.users],
            "scope": "global",
        }
    listed = await contacts_list(tg)
    needle = query.casefold()
    matched = [
        contact
        for contact in listed["contacts"]
        if (contact["display_name"] and needle in contact["display_name"].casefold())
        or (contact["username"] and needle in contact["username"].casefold())
    ]
    return {"contacts": matched, "scope": "local"}


def contacts_to_rows(data: dict) -> list[tuple]:
    return [
        (
            contact["id"],
            contact["type"],
            sanitize_plain_text(contact["username"]),
            sanitize_plain_text(contact["display_name"]),
        )
        for contact in data["contacts"]
    ]


MUTUAL_CHATS_LIMIT = 100


async def mutual_chats(tg, ref: str) -> dict:
    """List chats shared with a user (ADR-0032)."""
    try:
        entity = await tg.get_entity(chatref.parse(ref))
    except ValueError:
        raise NotFoundError(f"dialog not found: {ref!r}") from None
    peer = peer_to_dict(entity)
    if peer["type"] not in ("user", "bot"):
        raise PolicyError("mutual-chats requires a user or bot peer")
    input_user = await tg.get_input_entity(chatref.parse(ref))
    response = await tg(
        functions.messages.GetCommonChatsRequest(
            user_id=input_user,
            max_id=0,
            limit=MUTUAL_CHATS_LIMIT,
        )
    )
    chats = [peer_to_dict(chat) for chat in response.chats]
    return {"peer": peer, "chats": chats, "count": len(chats)}


def mutual_chats_to_rows(data: dict) -> list[tuple]:
    return [
        (
            chat["id"],
            chat["type"],
            sanitize_plain_text(chat["username"]),
            sanitize_plain_text(chat["display_name"]),
        )
        for chat in data["chats"]
    ]
