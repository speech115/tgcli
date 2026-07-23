"""Render quote degradations as prefixed body text (ADR-0036 / ADR-0037).

Synchronous and client-free: ``quotes`` decides when a quote cannot stay
native; this module shapes the plan, the body prefix, and the recorded loss.
"""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from typing import cast

from telethon.tl import types

from tgcli.clone import attribution, replies, transport


# Source label for a quote that could not stay a native reply. Russian, to
# match the clones this tool actually runs; one place to change.
FALLBACK_SOURCE_LABEL = "Переслано от:"


def peer_cache_key(peer) -> tuple:
    if isinstance(peer, types.PeerChannel):
        return ("channel", peer.channel_id)
    if isinstance(peer, types.PeerUser):
        return ("user", peer.user_id)
    if isinstance(peer, types.PeerChat):
        return ("chat", peer.chat_id)
    return (
        "other",
        getattr(peer, "channel_id", None) or getattr(peer, "user_id", None),
    )


def peer_label(peer, entity, title: str | None = None) -> str:
    if entity is not None:
        return attribution.display_name(entity)
    if title:
        return title
    if isinstance(peer, types.PeerChannel):
        return f"id {peer.channel_id}"
    if isinstance(peer, types.PeerUser):
        return f"id {peer.user_id}"
    if isinstance(peer, types.PeerChat):
        return f"id {peer.chat_id}"
    return "id unknown"


def reuploaded(plan: transport.TransportPlan) -> transport.TransportPlan:
    return replace(
        plan,
        mode="snapshots" if plan.mode == "snapshots" else "reuploaded",
        needs_author=True,
        reply_flattened=False,
    )


def fallback_prefix(title: str, quote_text: str | None) -> tuple[str, tuple]:
    """Labelled peer line, the quote as a blockquote, then a blank line."""
    quote = quote_text or ""
    head = f"{FALLBACK_SOURCE_LABEL} {title}\n"
    prefix = f"{head}{quote}\n\n"
    entities: tuple = ()
    if quote:
        entities = (
            types.MessageEntityBlockquote(
                offset=attribution.utf16_len(head),
                length=attribution.utf16_len(quote),
            ),
        )
    return prefix, entities


def fallback_plan(
    messages,
    plan: transport.TransportPlan,
    classified: replies.Classification,
    *,
    reason: str,
    entity=None,
    peer_title: str | None = None,
) -> transport.TransportPlan:
    title = peer_label(classified.peer, entity, peer_title)
    prefix, prefix_entities = fallback_prefix(title, classified.quote_text)
    quote_flattened = {
        "id": messages[0].id,
        "peer": peer_cache_key(classified.peer)[1],
        "reason": reason,
    }
    return reuploaded(
        replace(
            plan,
            reply_to=None,
            body_prefix=prefix,
            body_prefix_entities=prefix_entities,
            quote_flattened=quote_flattened,
        )
    )


def apply_body(message, author, plan) -> tuple[str, list | None]:
    """Quote fallback prefix (if any), then author attribution — one UTF-16 path."""
    text, entities = attribution.with_prefix(
        getattr(message, "message", None) or "",
        getattr(message, "entities", None),
        plan.body_prefix or "",
        plan.body_prefix_entities,
    )
    return attribution.prefixed(text, entities, author)


def _stale_quote_peer(messages):
    header = getattr(messages[0], "reply_to", None)
    peer = getattr(header, "reply_to_peer_id", None) if header is not None else None
    return None if peer is None else peer_cache_key(peer)[1]


def drop_stale_quote(messages, plan, error) -> transport.TransportPlan | None:
    """Strip a quote Telegram refuses, keeping the reply link intact.

    A quote carries the parent's text as it read when the quote was made. Edit
    the parent afterwards and the stored fragment no longer matches, so
    re-sending it is rejected with ``QUOTE_TEXT_INVALID`` even though the
    reply target itself is perfectly valid. Dropping the stale fragment keeps
    the reply — and the thread — and reports the loss; keeping it would fail
    the whole batch over one edited word.
    """
    if not str(getattr(error, "message", "") or "").startswith("QUOTE_"):
        return None
    reply_to = plan.reply_to
    if not isinstance(reply_to, types.InputReplyToMessage):
        return None
    if reply_to.quote_text is None:
        return None
    stripped = cast(types.InputReplyToMessage, copy(reply_to))
    stripped.quote_text = None
    stripped.quote_entities = None
    stripped.quote_offset = None
    return replace(
        plan,
        reply_to=stripped,
        quote_flattened={
            "id": messages[0].id,
            "peer": _stale_quote_peer(messages),
            "reason": "quote-rejected",
        },
    )
