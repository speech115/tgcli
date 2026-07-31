# ADR-0069: Keep Phase 5 archive exploration in a read-only query module

Date: 2026-07-31
Status: accepted (2026-07-31; implementation of ADR-0068 Phase 5)

## Context

ADR-0068 Phase 5 adds filtered FTS search, paging, an offline timeline, and
revision/tombstone inspection. The existing archive store already owns schema
and persistence, while `commands/archive.py` owns the CLI surface. Growing
those files with query shaping and payload rendering would mix ownership and
consume their reviewed architecture budgets.

## Decision

Add `src/tgcli/archive/explore.py` for read-only SQL query composition,
pagination, timeline/history selection, stored-payload rendering, snippets,
and `tg://` handoff links. It may read the existing v4 tables but adds no
schema, network path, background process, or mutation. `archive/search.py`
continues to own MATCH normalization and offline peer resolution; the command
module remains a thin alias/config/rows adapter.

## Rejected alternatives

- Put Phase 5 SQL and rendering in `store.py`: this would mix persistence and
  product queries in the first archive hotspot already marked for splitting.
- Put all behavior in `commands/archive.py`: this would make the CLI adapter
  own SQL and make direct offline query tests less precise.
- Add a search service or daemon: outside ADR-0068 and the no-daemon rule.

## Contract impact

`tg archive search`, `read`, and `history` remain offline and read-only. Their
flags and JSON/plain shapes are documented in CONTRACT.md §13. The module is
an implementation decomposition only; account scope, staleness semantics, and
the archive's read-only boundary do not change.
