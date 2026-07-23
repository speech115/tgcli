"""Decide how a clone batch travels: forward, reupload, or snapshot."""

from dataclasses import dataclass, replace

from tgcli.clone import fidelity, replies, topics


@dataclass(frozen=True)
class TransportPlan:
    mode: str
    reply_to: object | None
    reply_flattened: bool
    needs_author: bool
    # Quote fallback body (ADR-0036); applied before author attribution.
    body_prefix: str | None = None
    body_prefix_entities: tuple = ()
    # Per-batch quote fallback row for sync reporting (ADR-0036).
    quote_flattened: dict | None = None


def as_reuploaded(plan: TransportPlan) -> TransportPlan:
    """Make a plan upload-capable while preserving snapshot transport."""
    return replace(
        plan,
        mode="snapshots" if plan.mode == "snapshots" else "reuploaded",
        needs_author=True,
        reply_flattened=False,
    )


def decide(messages, leg, source) -> TransportPlan:
    classified = replies.target(messages, leg, source)
    header = getattr(messages[0], "reply_to", None)
    if classified is None:
        reply_to = None
        reply_flattened = False
    elif classified.kind == "mapped-in-leg":
        reply_to = replies.input_reply(classified, leg)
        reply_flattened = False
    elif classified.kind == "flatten":
        reply_to = None
        # Forum placement-only headers on a forum destination are not a lost reply.
        reply_flattened = not (
            leg.destination_kind == "forum" and topics.placement_only(header)
        )
    else:
        # foreign-peer / mapped-cross-leg: quotes.resolve fills reply_to or fallback.
        reply_to = None
        reply_flattened = True
    if len(messages) == 1 and fidelity.supports(messages[0]):
        mode = "snapshots"
    elif (
        getattr(source, "noforwards", False)
        or reply_to is not None
        or any(getattr(message, "noforwards", False) for message in messages)
    ):
        mode = "reuploaded"
    else:
        mode = "forwarded"
    needs_author = leg.source_kind != "broadcast" and mode != "forwarded"
    return TransportPlan(
        mode=mode,
        reply_to=reply_to,
        reply_flattened=reply_flattened,
        needs_author=needs_author,
    )
