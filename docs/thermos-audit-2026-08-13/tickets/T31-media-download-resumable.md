---
id: T31
priority: Debt
labels: [enhancement, ready-for-agent]
slice: Media & transfer
title: "Route media serial download through transfer.download_resumable"
---

## Problem

MAP assigns serial resumable download to `transfer.py`. Media keeps a
weaker fork (no fsync, different checkpoint cadence, FloodWait unwind
drift). Closely related to T03.

## Expected

`download_media` serial path calls `download_resumable`. Delete the fork.
Flatten parallel vs serial at the top. Prefer landing T03 regressions
here if T03 not yet done.

## Evidence

- Thermos Wave 6 quality + security
- ADR-0083 mirror-fix

## Acceptance

1. Serial media uses transfer helper; ADR-0083 tests ported.
2. Full gate green. Small-fix if no CONTRACT change.
