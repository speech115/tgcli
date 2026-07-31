# 2026-07-31 — Archive Phase 6 review fixes

## Scope

Closed the two Important findings from the independent Phase 6 review on
`codex/archive-phase6`. No subagents were used.

## Fixed

- Private dialogs are considered complete only after a real backfill records
  `last_backfill_at`; delta-only rows are eligible even when `more` is false.
  New private peers from the changes feed also start with `more: true`.
- Media acquisition now has independent `media_attempts` and
  `media_status` state. Three ordinary failures lead to terminal
  `no_media`; a successful publish resets the media episode. The v5→v6
  migration preserves existing media paths as `done`.
- Item-level media/transcription failures return `PartialFailure` data without
  incrementing the account outage streak. `FLOOD_WAIT` is re-raised without
  changing that streak; run-level failures retain the notification behavior.

## Verification

- Focused archive Phase 3/4/6 regressions: **34 passed**.
- Added permanent coverage for delta-only private state, v5→v6 migration,
  bounded media retry/terminal state, item failures, and rate limits.
- Live Telegram acceptance and integration release remain the integrator's
  next step; this session did not run a live mutation or backfill.
