"""Normalize CLI chat references before Telethon entity resolution.

Telethon treats a bare digit string as a phone number, so the documented
"dialog id" form must become an int before get_entity().
"""

from telethon import utils


def parse(chat: str) -> int | str:
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat


async def resolve(tg, chat: str):
    """Resolve a read target without accepting a different marked peer.

    Telethon's memory cache is keyed by bare ids, so even a negative marked
    id can resolve to a user or another chat type with the same bare id.
    Validate the full peer identity before a caller reads or returns data.
    Positive bare ids keep the CLI's existing, type-ambiguous resolution.
    """
    ref = parse(chat)
    entity = await tg.get_entity(ref)
    if isinstance(ref, int) and ref < 0:
        if utils.get_peer_id(entity) != ref:
            raise ValueError("resolved peer does not match the marked dialog id")
    return entity
