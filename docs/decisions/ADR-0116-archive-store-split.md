# ADR-0116: Split archive store persistence; one peer-identity read seam

Date: 2026-08-13
Status: accepted
Form: lite (new modules; no CONTRACT/safety change)
Owner request: thermos debt T30
Amends: [ADR-0068](ADR-0068-local-archive-store.md) module layout;
[ADR-0069](ADR-0069-archive-exploration-module.md) identity reads

## Context

`archive/store.py` sat at its hard architecture ceiling (~1073 lines) and
mixed schema/migrations, message/FTS writes, transcript queue, scope rows,
peer `sync_state`, and account sync. Explore/search also duplicated
scope⊕sync_state identity reads (`COALESCE` joins in SQL and a Python
fallback), so a third call site would widen the sprawl.

## Decision

1. Decompose persistence into peer modules under `tgcli.archive`:
   - `schema.py` — constants, DDL, connect/migrations, meta, paths;
   - `messages.py` — message upsert, tombstones, peer lookup, counts;
   - `transcripts.py` — transcript queue/status and FTS transcript column;
   - `sync_state.py` — explicit scope rows, peer sync_state, account_sync;
   - `peers.py` — the single identity read seam (`resolve`, resolve-row
     listing, `known`, shared `IDENTITY_SELECT` / `IDENTITY_JOINS`);
   - `store.py` — thin facade re-exporting the historical public surface.
2. Explore and search call `peers.resolve` / `peers.list_resolve_rows` /
   `peers.known` and reuse the SQL fragments instead of local COALESCE
   copies. Ingest pipelines are not rewritten in this slice.
3. Seed architecture ceilings on the new modules at split size and lower
   the `store.py` facade ceiling so the monolith cannot return unnoticed.

## Rejected alternatives

- Keep one file and only raise the ceiling: rejected; T30 exists to remove
  the hotspot, not ratify it.
- Rewrite backfill/sync into a shared ingest pipeline now: rejected as
  YAGNI; that is a later ticket.
- Drop the `store` facade and force every caller to import leaf modules:
  rejected; behavior-preserving import stability is part of the split.

## Contract impact

None. CLI flags, JSON shapes, exit codes, schema version, and mutation
safety are unchanged. `docs/CONTRACT.md`, versions, and `CHANGELOG.md`
stay untouched.
