# 2026-07-31 — Archive Phase 3: private backfill + delta sync

## Goal

Ship ADR-0068 Phase 3 on `codex/archive-phase3`: standing private
enumeration under budgets, `tg archive sync` via the existing
`tg changes` cursor, gap/rebaseline, private `--chat` resolve, and live
tombstone acceptance.

## Done

- Schema v3: `account_sync` (cursor/gap/reconcile) + `sync_state` identity
  columns; v1/v2 migrate in place.
- `archive search --chat @username` resolves private peers from
  `sync_state` identity (PoV gap).
- `tg archive backfill --private [--max-dialogs N] [--limit N]` with hard
  caps and skip-complete; no empty→all sentinel.
- `tg archive sync` / `rebaseline`: reuse `changes.once` (exact
  GetDifference / GetChannelDifference); apply new/edit/delete + scoped
  channel catch-up; private deletes via `private_deletes=True` + local id
  resolve; light reconcile sample.
- CONTRACT §13, guide/archive, SKILL, MAP, FEATURES, PROPOSALS updated.
- Live acceptance on `--account main` Saved Messages (`me`): send →
  backfill → edit → sync (revision) → delete → sync (tombstone) for
  message id 284561. Sync JSON after the delete pass reported
  `applied.tombstones >= 1` and `status` counts showed a tombstone row;
  no session material or message text retained here. Fake-proven apply
  path remains in `tests/test_cli_archive_phase3.py`.

## Gate

`./scripts/gate.sh` — all checks passed (1597 passed, 9 skipped).
Architecture grace warnings (integrator ratchets at merge):

- `parser.py` 645 / ceiling 615
- `preflight.py` 353 / ceiling 328
- `dispatch.py` 315 / ceiling 299

## Not done / next

- Phase 4 media + transcription; Phase 5 full search UX.
- Edits delivered as `message_new` now increment `applied.edits` when
  upsert returns `updated` (observed live).
