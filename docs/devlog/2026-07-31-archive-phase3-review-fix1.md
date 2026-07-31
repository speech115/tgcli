# 2026-07-31 — Archive Phase 3 review-fix1 (I1–I4)

## Goal

Close Important findings from the independent Phase 3 review on
`codex/archive-phase3` before merge sign-off.

## Done

- I1: CLI tests for `archive sync` over-cap `--max-events` / `--max-dialogs`
  (and `--max-dialogs 0`) → exit 2.
- I2: CONTRACT §13 + guide sentence on private-delete peer ambiguity;
  regression test that the same `message_id` in two peers yields two
  tombstones.
- I3: Fake CLI test for scoped `channel_activity` → catch-up upsert with
  bounded `iter_messages` / `min_id`.
- I4: Account-guard coverage for `archive sync` and `rebaseline`
  (mismatch → exit 2).
- Minors: Phase 3 plan checkboxes marked done; dead `apply_events` branch
  removed; Phase 3 diglog live-acceptance outcome note strengthened.

## Gate

`./scripts/gate.sh` — all checks passed (1601 passed, 9 skipped).
Architecture grace warnings unchanged (integrator ratchets at merge):

- `parser.py` 645 / ceiling 615
- `preflight.py` 353 / ceiling 328
- `dispatch.py` 315 / ceiling 299

## Not done / next

- M1 (sync FLOOD_WAIT on catch-up) left optional/untested.
- Integrator: ceiling ratchets + release bump per ADR-0058/0038 on merge.
