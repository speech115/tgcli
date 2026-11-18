"""Send intents: adopt what a crashed run sent but never recorded.

Before a batch or a caption rest goes out, its leg's intent (the source ids
it copies) is saved; the save that maps the confirmed copies clears it. A run
killed between Telegram's send and that save leaves the intent behind, and
the next run's tail guard would otherwise refuse the unrecorded copies.

``guard_tail`` settles a leftover intent first. Past the recorded tail it
finds either nothing (the send never landed, so the intent is dropped and the
batch is sent again) or exactly the copies the intent names, which it maps in
id order. Anything else stays unexplained and the tail guard refuses it, as
before: manual repair.
"""

from __future__ import annotations

from tgcli.clone import discussion, state
from tgcli.clone.legs import AnyLeg
from tgcli.errors import PolicyError
from tgcli.output import note


def begin(clone_state: state.CloneState, leg: AnyLeg, kind: str, sources) -> None:
    """Save the intent before the send it names."""
    clone_state.send_intents[leg.kind] = {"kind": kind, "sources": list(sources)}
    state.save(clone_state)


def settle(clone_state: state.CloneState, leg: AnyLeg) -> None:
    """Drop the intent; the caller's next save makes that durable together
    with the mapping it recorded."""
    clone_state.send_intents.pop(leg.kind, None)


async def guard_tail(tg, destination, leg: AnyLeg, recorded_last_id, label, expected):
    """The tail guard, after adopting what a leftover intent explains."""
    clone_state = leg.clone_state
    intent = clone_state.send_intents.get(leg.kind)
    unexpected = await discussion.unexpected_tail(
        tg, destination, recorded_last_id, label, expected
    )
    if intent is not None:
        sources = intent["sources"]
        if not unexpected:
            settle(clone_state, leg)
            state.save(clone_state)
            return
        if len(unexpected) == len(sources):
            if intent["kind"] == "rest":
                leg.record_raw_caption_rest(sources[0], unexpected[0].id)
            else:
                for source_id, item in zip(sources, unexpected, strict=True):
                    leg.record_raw_mapping(source_id, item.id)
                leg.cursor = max(leg.cursor, sources[-1])
            settle(clone_state, leg)
            state.save(clone_state)
            note(
                f"clone {label}: adopted {len(sources)} message(s) an interrupted "
                "run sent but did not record"
            )
            return
    if unexpected:
        raise PolicyError(
            f"clone {label} has unexpected tail messages; manual repair is required",
            unexpected=len(unexpected),
        )
