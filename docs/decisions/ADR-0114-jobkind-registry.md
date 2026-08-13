# ADR-0114: JobKind registry and shared lane loop helpers

Date: 2026-08-13
Status: accepted (owner request: thermos debt T33)
Form: ADR-lite (internal refactor)

## Context

ADR-0087's four typed workloads duplicated lane/kind dispatch as string
literals across `jobs/preflight.py`, `jobs/model.py`, and
`jobs/runner.py`. `run_local` and `run_telegram` also copied the same
foreground loop skeleton and the RateLimit/FloodWait cooldown deferral
path. Thermos Wave 3 ranked this as structural debt before a fifth
workload lands.

## Decision

1. Add a `JobKind` registry in `jobs/model.py` (`JOB_KINDS`,
   `require_kind`) as the single source for kind→lane mapping.
2. Extract `_run_lane_loop` / `_run_lane_loop_async`,
   `_defer_for_cooldown`, and `_finish_from_result` in `jobs/runner.py`
   so both lanes share claim/recover/outcome recording and cooldown
   deferral without changing CLI or registry semantics.
3. `jobs/preflight.py` reads lane assignment from the registry; per-kind
   spec normalization stays local to preflight until a fifth kind forces
   a richer table.

## Rejected alternatives

- Moving CLI argument grammar into the registry — each kind still owns
  distinct flags; the registry only centralizes lane metadata.
- A `tracks_progress` flag on `JobKind` — unused YAGNI; progress stays
  in runner result handling until a reader appears.
- One async runner for both lanes — the local lane stays synchronous and
  opens no Telegram session.

## Contract impact

None. Job states, requeue reasons, cooldown deferral, and stdout JSON
unchanged.
