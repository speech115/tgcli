---
id: T13
priority: P1
labels: [bug, ready-for-agent]
slice: Safety & mutations
title: "Expired --commit must not sticky-pending out of cleanup"
---

## Problem

`begin_commit` validates kind, renames `.json` → `.pending`, then checks
TTL. An expired preview becomes sticky pending. Default `store cleanup`
skips pending; `--include-pending` waits until `expires_at + PREVIEW_TTL`.
PII in the preview stays on disk longer than intended.

## Expected

TTL check before rename, or rename-back / classify expired pending as
reapable under default cleanup. Preview text must not escape the normal
expired bucket.

## Evidence

- `src/tgcli/safety.py` (`begin_commit`)
- store cleanup buckets
- Thermos Wave 1 security Medium#1

## Acceptance

1. Test: expired commit leaves nothing outside default cleanup policy.
2. Kind-mismatch still preserves `.json` (existing behavior).
3. Full gate green.
