# 2026-07-31 — Archive Phase 6 refresh

## Scope

Continued the owner-requested archive Phase 6 from `v1.2.24` on
`codex/archive-phase6`. No subagents were used.

## Implemented

- Added `tg archive refresh` as a bounded foreground composition of archive
  sync (including its media stage) and the offline transcription queue.
- Threaded the existing per-run FloodWait budget through the composition and
  added bounded flags for sync/media/transcription work.
- Added schema v5 migration fields on `account_sync` for a consecutive refresh
  failure streak, last error, and one-notification episode marker.
- Added best-effort generic macOS notification through `desktop.py` after
  three consecutive failed runs; a successful run resets the episode.
- Added a manual hourly launchd plist template and a task guide. The template
  is not installed or loaded automatically.
- Updated CONTRACT, SKILL, MAP, README, FEATURES, PROPOSALS, guide index, and
  ADR-0070.

## Verification

- Focused Phase 6 + archive/desktop regression suite: **78 passed**.
- Full gate: **1630 passed, 9 skipped**; coverage matrix passed for 23
  namespaces.
- `scripts/check-architecture.py`: passed; Phase 6 growth remains inside the
  existing grace bands, with new module ceilings left for the integrator.
- `scripts/check-docs.py`: passed.
- `ruff check src tests`: passed.
- `ruff format --check src tests`: passed.
- `pyright`: 0 errors, 0 warnings, 0 informations.

Live owner acceptance of `tg archive refresh` against the real account and
the release/tag are intentionally still pending integration.
