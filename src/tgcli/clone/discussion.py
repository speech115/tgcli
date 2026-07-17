"""Discussion groups: detection, linking and anchors for comment clones."""
from telethon import errors as telethon_errors
from telethon.tl import functions, types
from tgcli.clone import topics
from tgcli.errors import PolicyError


def linked_chat_id(full_channel) -> int | None:
    """ChannelFull.linked_chat_id only. linked_monoforum_id is a monoforum,
    not a comment section (live-proven on @groks) — never read it."""
    chat_id = getattr(full_channel, "linked_chat_id", None)
    if type(chat_id) is not int or chat_id <= 0:
        return None
    return chat_id


def is_discussion_destination(entity, *, title: str | None = None) -> bool:
    """Private owned megagroup that is not a forum."""
    return (topics.is_forum_destination(entity, title=title)
            and not getattr(entity, "forum", False))


def autoforward_post_id(message, source_channel_id: int) -> int | None:
    """Source post id if message is Telegram's auto-forward anchor for
    source_channel_id, else None. Matches fwd_from.saved_from_peer +
    saved_from_msg_id (live-proven)."""
    header = getattr(message, "fwd_from", None)
    if not isinstance(header, types.MessageFwdHeader):
        return None
    peer = header.saved_from_peer
    if (not isinstance(peer, types.PeerChannel)
            or peer.channel_id != source_channel_id):
        return None
    post_id = header.saved_from_msg_id
    if type(post_id) is not int or not 0 < post_id <= 2_147_483_647:
        return None
    return post_id


async def verify_tail(tg, destination, recorded_last_id, label, expected) -> None:
    """Tail guard shared by both clone legs: nothing may sit past the recorded
    tail unless `expected` vouches for it (service messages on the channel,
    Telegram's own auto-forwards in the discussion group)."""
    latest = await tg.get_messages(destination, limit=1)
    destination_last_id = latest[0].id if latest else 0
    if recorded_last_id is not None and recorded_last_id > destination_last_id:
        raise PolicyError(
            f"clone {label} recorded tail is missing; manual repair is required")
    baseline = recorded_last_id or 1
    if destination_last_id <= baseline:
        return
    tail = await tg.get_messages(destination, limit=destination_last_id - baseline)
    unexpected = [item for item in tail if item.id > baseline and not expected(item)]
    if unexpected:
        raise PolicyError(
            f"clone {label} has unexpected tail messages; manual repair is required",
            unexpected=len(unexpected))


async def adopt(tg, mutate, marker_candidates, marker, recorded_peer_id, on_create):
    """The recorded peer, else the single marker-matched group, else a fresh
    one. Same marker discipline as the destination channel."""
    if recorded_peer_id is not None:
        try:
            return await tg.get_entity(types.PeerChannel(recorded_peer_id))
        except ValueError:
            raise PolicyError("clone discussion group is unavailable") from None
    valid, wrong_shape = await marker_candidates(marker, is_discussion_destination)
    if len(valid) + len(wrong_shape) > 1:
        raise PolicyError("clone discussion marker matched multiple groups")
    if wrong_shape:
        raise PolicyError("clone discussion marker matched a group with wrong shape")
    if valid:
        return valid[0]
    on_create()
    update = await mutate(topics.create_request(marker))
    candidates = [item for item in getattr(update, "chats", ())
                  if is_discussion_destination(item, title=marker)]
    if len(candidates) != 1:
        raise PolicyError("Telegram did not return the created discussion group")
    return candidates[0]


async def ensure_linked(mutate, channel, group) -> None:
    """Idempotent: unhide pre-history, then SetDiscussionGroupRequest. Telegram
    answers a no-op with an error rather than silence, and both no-ops are the
    normal path (live-proven): a megagroup it just created already shows its
    history, and crash recovery re-links an already-linked pair."""
    for request, benign in (
            (functions.channels.TogglePreHistoryHiddenRequest(
                channel=group, enabled=False),
             telethon_errors.ChatNotModifiedError),
            (functions.channels.SetDiscussionGroupRequest(
                broadcast=channel, group=group),
             telethon_errors.LinkNotModifiedError)):
        try:
            await mutate(request)
        except benign:
            pass


async def anchor_for(mutate, destination_channel, destination_post_id: int,
                     cache: dict) -> int | None:
    """Destination anchor message id in the discussion group, via
    messages.getDiscussionMessage. Cached per run; None when Telegram
    returns no anchor."""
    if destination_post_id in cache:
        return cache[destination_post_id]
    response = await mutate(functions.messages.GetDiscussionMessageRequest(
        peer=destination_channel, msg_id=destination_post_id))
    anchors = [getattr(item, "id", None)
               for item in (getattr(response, "messages", None) or ())]
    found = [item for item in anchors
             if type(item) is int and 0 < item <= 2_147_483_647]
    anchor = found[0] if found else None
    cache[destination_post_id] = anchor
    return anchor
