"""Validate and rebuild clone reply metadata (ADR-0019/0021)."""

from telethon.tl import types

from tgcli.clone import attribution
from tgcli.errors import PolicyError


def _signature(header, source, forum=False):
    if header is None:
        return None
    if isinstance(header, types.MessageReplyStoryHeader):
        peer = header.peer
        peer_id = getattr(peer, "user_id", None) or getattr(peer, "channel_id", None)
        if (
            peer_id is None
            or isinstance(header.story_id, bool)
            or not isinstance(header.story_id, int)
            or header.story_id <= 0
        ):
            raise PolicyError("clone Story reply shape is not supported")
        return ("story", type(peer).__name__, peer_id, header.story_id)
    if not isinstance(header, types.MessageReplyHeader):
        raise PolicyError("clone reply shape is not supported")
    unsupported = ("todo_item_id", "poll_option", "reply_from", "reply_media")
    if (
        header.reply_to_scheduled
        or header.reply_to_ephemeral
        or any(getattr(header, field) is not None for field in unsupported)
    ):
        raise PolicyError("clone reply shape is not supported")
    peer = header.reply_to_peer_id
    if peer is not None and not attribution.same_peer(peer, source):
        raise PolicyError("cross-peer clone replies are not supported")
    parent_id, top_id = header.reply_to_msg_id, header.reply_to_top_id
    if parent_id is None or any(
        isinstance(item, bool)
        or not isinstance(item, int)
        or not 0 < item <= 2_147_483_647
        for item in (parent_id, top_id)
        if item is not None
    ):
        raise PolicyError("clone reply parent is invalid")
    if (
        header.quote_text is not None
        and not isinstance(header.quote_text, str)
        or header.quote_entities
        and header.quote_text is None
        or header.quote_offset is not None
        and (
            header.quote_text is None
            or isinstance(header.quote_offset, bool)
            or not isinstance(header.quote_offset, int)
            or header.quote_offset < 0
        )
    ):
        raise PolicyError("clone reply quote is invalid")
    if header.forum_topic:
        if not forum:
            raise PolicyError("clone reply shape is not supported")
        if header.reply_to_top_id is None:
            return ("forum-place", parent_id)
        return (
            "forum-reply",
            top_id,
            parent_id,
            header.quote_text,
            tuple(header.quote_entities or ()),
            header.quote_offset,
        )
    return (
        parent_id,
        top_id,
        header.quote_text,
        tuple(header.quote_entities or ()),
        header.quote_offset,
    )


def target(messages, leg, source):
    forum = leg.destination_kind == "forum"
    signatures = [
        _signature(getattr(message, "reply_to", None), source, forum)
        for message in messages
    ]
    leading = signatures[0]
    if leading is None and any(item is not None for item in signatures[1:]):
        raise PolicyError("clone album reply appears after its leading item")
    if leading is not None and any(
        item is not None and item != leading for item in signatures[1:]
    ):
        raise PolicyError("clone album reply metadata is inconsistent")
    if leading is None:
        return None
    if leading[0] in {"story", "forum-place"}:
        return None
    if leading[0] == "forum-reply":
        _, _, parent_id, quote_text, quote_entities, quote_offset = leading
        top_id = None
    else:
        parent_id, top_id, quote_text, quote_entities, quote_offset = leading
    destination_id = leg.dest_for(parent_id)
    if destination_id is None:
        return None
    top_destination_id = leg.dest_for(top_id) if top_id is not None else None
    return types.InputReplyToMessage(
        reply_to_msg_id=destination_id,
        top_msg_id=top_destination_id,
        quote_text=quote_text,
        quote_entities=list(quote_entities) or None,
        quote_offset=quote_offset,
    )
