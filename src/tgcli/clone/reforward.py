"""Native re-forward of a reposted post out of the source discussion group.

ADR-0050 Part B. A protected channel post that is itself a forward loses its
native header on the reupload path, and Part A puts a truthful text prefix in
its place. When the linked *source* group still holds the message the post was
reposted from, and that group is not itself protected, forwarding that message
reproduces Telegram's own header instead.

The clone only does so when the original is proven: a reachable unprotected
group, exactly one group message from ``fwd_from.from_id`` at
``fwd_from.date``, and content identical to the post. Any doubt falls back to
the Part A prefix — a repost is routinely edited afterwards, and republishing
the unedited original would silently publish different text.
"""

from datetime import timedelta

from telethon import errors as telethon_errors
from telethon.tl import types


# One messages.Search per reposted post against the ADR-0045 flood budget. The
# window only has to cover the messages a single sender produced within one
# second; candidates outside `fwd_from.date` are discarded anyway.
SEARCH_LIMIT = 20

_GROUP_KEY = "source-group"


def eligible(leg, messages, plan) -> bool:
    """Whether a batch may spend a search on proving its original.

    Only a lone protected broadcast repost with nothing else in flight
    qualifies. Albums are excluded — every item would need its own proof —
    and so are snapshots, whose poll-vote replication (ADR-0048) is built on
    the rendered placeholder the forward would replace.
    """
    if plan.mode != "reuploaded" or leg.source_kind != "broadcast":
        return False
    if len(messages) != 1 or plan.reply_to is not None:
        return False
    if plan.body_prefix or plan.quote_flattened is not None:
        return False
    return _searchable(getattr(messages[0], "fwd_from", None)) is not None


async def locate(tg, clone_state, message, cache: dict, *, invoke):
    """``(group, message_id)`` to forward natively, or None to keep the prefix.

    Fallback-first: every condition that fails to hold answers None, and the
    caller renders the Part A attribution instead.
    """
    searchable = _searchable(getattr(message, "fwd_from", None))
    if searchable is None:
        return None
    peer, date = searchable
    group = await source_group(tg, clone_state, cache, invoke=invoke)
    if group is None:
        return None
    found = await _guarded(
        invoke,
        tg.get_messages(
            group,
            from_user=peer,
            offset_date=date + timedelta(seconds=1),
            limit=SEARCH_LIMIT,
        ),
    )
    dated = [item for item in (found or ()) if getattr(item, "date", None) == date]
    if len(dated) != 1:
        return None
    candidate = dated[0]
    if not _same_content(message, candidate):
        return None
    return group, candidate.id


async def source_group(tg, clone_state, cache: dict, *, invoke):
    """The source discussion group entity, resolved once per sync run.

    None when the clone has no linked source group, when it is unreachable
    (never joined, left), or when it is itself protected — nothing can be
    forwarded out of it, so no search is spent on it either.
    """
    if _GROUP_KEY in cache:
        return cache[_GROUP_KEY]
    peer_id = getattr(clone_state, "discussion_source_peer_id", None)
    group = None
    if peer_id is not None:
        group = await _guarded(invoke, tg.get_entity(types.PeerChannel(peer_id)))
        if group is not None and getattr(group, "noforwards", False):
            group = None
    cache[_GROUP_KEY] = group
    return group


def _searchable(fwd):
    """``(peer, date)`` when `fwd_from` names a peer and a moment to search by.

    A hidden account (`from_name` only) is unsearchable by construction, and
    the clone must not guess which message it was.
    """
    peer = getattr(fwd, "from_id", None)
    date = getattr(fwd, "date", None)
    if not isinstance(peer, (types.PeerUser, types.PeerChannel)) or date is None:
        return None
    return peer, date


def _same_content(post, candidate) -> bool:
    """Text and media identical. Load-bearing: the live `[икона]` 69 case was
    reposted at 13:55 and edited at 16:18, and forwarding the untouched
    original would republish different words under a genuine header."""
    if (getattr(post, "message", "") or "") != (
        getattr(candidate, "message", "") or ""
    ):
        return False
    return _media_key(post) == _media_key(candidate)


def _media_key(message):
    media = getattr(message, "media", None)
    if media is None:
        return None
    for attribute in ("photo", "document", "webpage"):
        carried = getattr(media, attribute, None)
        if carried is not None:
            return type(media).__name__, attribute, getattr(carried, "id", None)
    return type(media).__name__, None, None


async def _guarded(invoke, awaitable):
    """Fall back on any refusal, but never swallow a FloodWait: ADR-0045 needs
    it to arm the cooldown and stop the run."""
    try:
        return await invoke(awaitable)
    except telethon_errors.FloodWaitError:
        raise
    except (ValueError, telethon_errors.RPCError):
        return None
