"""Body-prefix backfill eligibility and candidate scan (ADR-0054)."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from tgcli.clone import attribution, fidelity, quote_fallback, transport


def _entities_equal(left, right) -> bool:
    left_list = list(left or ())
    right_list = list(right or ())
    if len(left_list) != len(right_list):
        return False
    return all(a.to_dict() == b.to_dict() for a, b in zip(left_list, right_list))


def eligible_for_backfill(
    message, dest_text, dest_entities, rendered_text, rendered_entities
) -> bool:
    """True only when dest is the unprefixed source body and a prefix is due."""
    source_text = getattr(message, "message", None) or ""
    source_entities = getattr(message, "entities", None)
    if (dest_text or "") != source_text:
        return False
    if not _entities_equal(dest_entities, source_entities):
        return False
    if (rendered_text or "") == source_text and _entities_equal(
        rendered_entities, source_entities
    ):
        return False
    return True


async def render_with_current_rules(
    tg, source, message, me, source_kind: str, cache: dict, cooldown
):
    """Reuse sync's body renderer: source_kind-branched attribution + apply_body.

    Mirrors the sync send path: only a broadcast post that is itself a forward
    takes the ADR-0050 forward lead; every other source kind keeps the
    ``author_of`` speaker label, exactly as sync would render it today.
    """
    author = None
    if source_kind == "broadcast":
        if getattr(message, "fwd_from", None) is not None:
            author = await attribution.forwarded_author_of(tg, message, cache, cooldown)
    else:
        author = await attribution.author_of(tg, source, message, me, cache, cooldown)
    plan = transport.TransportPlan(
        mode="reuploaded",
        reply_to=None,
        reply_flattened=False,
        needs_author=True,
    )
    return quote_fallback.apply_body(message, author, plan)


@dataclass(frozen=True)
class Candidate:
    source_id: int
    destination_id: int
    text: str
    entities: list | None


@dataclass(frozen=True)
class Excluded:
    source_id: int
    reason: str


def _album_lead_status(source_id, message, ordered_ids, source_by_id) -> str:
    """Prove "lead", "follower", or "unknown" via the adjacent mapped post.

    Albums are formed only from adjacent messages in the copied mapped stream
    (clone/batching.py), so the nearest lower mapped source id decides: the
    same grouped_id proves a follower, anything else proves the lead. A
    neighbor Telegram did not return proves nothing — fail closed rather than
    prefix the wrong live message.
    """
    index = bisect_left(ordered_ids, source_id)
    if index == 0:
        return "lead"
    neighbor = source_by_id.get(ordered_ids[index - 1])
    if neighbor is None:
        return "unknown"
    if getattr(neighbor, "grouped_id", None) == getattr(message, "grouped_id", None):
        return "follower"
    return "lead"


async def candidates(
    tg, clone_state, source_entity, destination_entity, me, cooldown
) -> tuple[list[Candidate], list[Excluded]]:
    """Scan posts-leg id_map for prefix-backfill candidates (ADR-0054)."""
    mapping = [
        (int(source_id), destination_id)
        for source_id, destination_id in clone_state.id_map.items()
    ]
    if not mapping:
        return [], []
    source_ids = [source_id for source_id, _ in mapping]
    dest_ids = [destination_id for _, destination_id in mapping]
    source_msgs = await cooldown(lambda: tg.get_messages(source_entity, ids=source_ids))
    dest_msgs = await cooldown(
        lambda: tg.get_messages(destination_entity, ids=dest_ids)
    )
    source_by_id = {
        source_id: message
        for source_id, message in zip(source_ids, source_msgs, strict=True)
        if message is not None
    }
    dest_by_id = {
        dest_id: message
        for dest_id, message in zip(dest_ids, dest_msgs, strict=True)
        if message is not None
    }
    ordered_source_ids = sorted(source_ids)
    author_cache: dict = {}
    eligible: list[Candidate] = []
    excluded: list[Excluded] = []
    for source_id, destination_id in mapping:
        message = source_by_id.get(source_id)
        dest = dest_by_id.get(destination_id)
        if message is None or dest is None:
            continue
        if getattr(message, "fwd_from", None) is None:
            continue
        if fidelity.supports(message):
            excluded.append(Excluded(source_id=source_id, reason="poll-snapshot"))
            continue
        if getattr(dest, "fwd_from", None) is not None:
            excluded.append(Excluded(source_id=source_id, reason="native-reforward"))
            continue
        if getattr(message, "grouped_id", None) is not None:
            status = _album_lead_status(
                source_id, message, ordered_source_ids, source_by_id
            )
            if status == "unknown":
                excluded.append(
                    Excluded(source_id=source_id, reason="album-lead-unknown")
                )
                continue
            if status == "follower":
                excluded.append(Excluded(source_id=source_id, reason="album-non-lead"))
                continue
        rendered_text, rendered_entities = await render_with_current_rules(
            tg,
            source_entity,
            message,
            me,
            clone_state.source_kind,
            author_cache,
            cooldown,
        )
        dest_text = getattr(dest, "message", None) or ""
        dest_entities = getattr(dest, "entities", None)
        if not eligible_for_backfill(
            message, dest_text, dest_entities, rendered_text, rendered_entities
        ):
            excluded.append(Excluded(source_id=source_id, reason="not-eligible"))
            continue
        eligible.append(
            Candidate(
                source_id=source_id,
                destination_id=destination_id,
                text=rendered_text,
                entities=rendered_entities,
            )
        )
    return eligible, excluded
