# FABLE5 Hardening Campaign — Progress Journal

Durable cross-session source of truth for the `claude/fable5-hardening-5zqywp`
campaign. Updated after every completed slice. Newest state on top of each
section.

Campaign goal: correctness, security, recoverability, stability, and
maintainability. No new features. Prefer deleting complexity, local fixes,
provable invariants.

## Current state

- Branch: `claude/fable5-hardening-5zqywp` (exists on origin)
- Base: `main` @ `dca1eed` (1.2.15)
- HEAD: (updated per slice)
- Baseline gate at `dca1eed`, run 2026-07-26 in this environment:
  ruff check passed; ruff format 147 files clean; architecture check passed;
  pyright 0 errors; **pytest 1132 passed, 9 skipped**; coverage OK
  (23 namespaces); docs gate 23 pages, 0 problems.

## Plan of record

1. **Phase 1 — baseline + five handoff defects** (permissions, FloodWait
   gate, pin crash recovery, refresh attribution parity, deleted album
   lead). Each: failing regression test → minimal fix → focused tests →
   full gate → independent review → commit+push.
2. **Phase 2 — full evidence-based audit** (multi-lens workflow with
   adversarial verification of every finding).
3. **Phase 3 — fixes P0→P2 + simplification**, including the
   owner-approved `commands/clone.py` split behind characterization tests.
4. **Phase 4 — release slice 1.2.16**, whole-branch adversarial review,
   full gate, PR to main, CI. No merge without the owner.

Owner decisions already taken (2026-07-26):

- Do **not** touch the fork `oneaipro128-jpg/tgcli`; reconstruct the five
  defects from the handoff specifications on this branch.
- Deep audit budget approved.
- `clone.py` split approved for this campaign (after Phase 1, with
  characterization tests first).
- No live Telegram mutations. Read-only smoke only if a safe test
  environment exists.

## Confirmed findings

- (pending — Phase 1 defect verification in progress)

## Completed commits

- (none yet)

## Checks performed

- 2026-07-26 baseline `./scripts/gate.sh` at `dca1eed`: all green, exact
  results in "Current state" above.

## Open risks / blockers

- Handoff defect 4 (refresh attribution parity) was never confirmed
  complete in the old session; treated as fully open.
- Handoff residual gaps recorded for Phase 2 triage:
  `login_state.promote()` creates `sessions/` without an enforced mode;
  pin recovery equality fails if the source pin changed during the crash
  window (needs a persisted `pin_pending` intent — separate schema work).
- Release-tag policy drift (ADR-0038 promises tags; none exist upstream)
  — owner decision needed, recorded under "Decisions needing the owner".

## Unfinished work

- Phase 1 defect slices: not started (verification next).

## Next concrete step

- Launch the Phase-1 defect verification/fix workflow (four isolated
  scopes: D1 permissions, D2 flood gate, D3 pin recovery, D4+D5 refresh),
  then integrate serially with a full gate per slice.
- Command: `./scripts/gate.sh` after each integrated slice.

## Decisions needing the owner

- Release-tag policy: create the promised `v1.2.x` tags or amend ADR-0038
  so CHANGELOG is the single source of truth (upstream currently has no
  tags). Deferred to the Phase-4 report.
- Merge of the campaign PR (never without the owner).
