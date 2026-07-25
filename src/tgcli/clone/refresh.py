"""Body-prefix backfill eligibility and candidate scan (ADR-0054)."""

from __future__ import annotations

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


async def render_with_current_rules(tg, message, cache: dict, cooldown):
    """Reuse sync's body renderer: forwarded_author_of + apply_body."""
    author = None
    if getattr(message, "fwd_from", None) is not None:
        author = await attribution.forwarded_author_of(tg, message, cache, cooldown)
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


def _album_leads(source_by_id: dict[int, object]) -> set[int]:
    """Lowest source id per grouped_id among mapped posts — the album lead."""
    leads: dict[object, int] = {}
    for source_id, message in source_by_id.items():
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is None:
            continue
        current = leads.get(grouped_id)
        if current is None or source_id < current:
            leads[grouped_id] = source_id
    return set(leads.values())


async def candidates(
    tg, clone_state, source_entity, destination_entity, cooldown
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
    album_leads = _album_leads(source_by_id)
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
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is not None and source_id not in album_leads:
            excluded.append(Excluded(source_id=source_id, reason="album-non-lead"))
            continue
        rendered_text, rendered_entities = await render_with_current_rules(
            tg, message, author_cache, cooldown
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
