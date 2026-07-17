"""Decide how a clone batch travels: forward, reupload, or snapshot."""

from dataclasses import dataclass

from tgcli.clone import fidelity, replies, topics


@dataclass(frozen=True)
class TransportPlan:
    mode: str
    reply_to: object | None
    reply_flattened: bool
    needs_author: bool


def decide(messages, leg, source) -> TransportPlan:
    reply_to = replies.target(messages, leg, source)
    header = getattr(messages[0], "reply_to", None)
    reply_flattened = (header is not None and reply_to is None
                       and not topics.placement_only(header))
    if len(messages) == 1 and fidelity.supports(messages[0]):
        mode = "snapshots"
    elif (getattr(source, "noforwards", False) or reply_to is not None
            or any(getattr(message, "noforwards", False)
                   for message in messages)):
        mode = "reuploaded"
    else:
        mode = "forwarded"
    needs_author = (leg.source_kind != "broadcast"
                    and mode != "forwarded")
    return TransportPlan(mode=mode, reply_to=reply_to,
                         reply_flattened=reply_flattened,
                         needs_author=needs_author)
