---
id: T10
priority: P1
labels: [bug, ready-for-agent]
slice: Archive
title: "Honor --max-runtime / stop through archive sync + media"
---

## Problem

Jobs pass `should_stop` into sync; CLI `archive sync` does not honor
`--max-runtime` / wall-clock. Backfill stops between dialogs on
wall-clock, then still runs `fetch_media` uncapped. Defeats the normal
stop contract.

## Expected

Thread `pacing.wall_clock_remaining` / `should_stop` through sync
catch-up and all `fetch_media` call sites (CLI sync + backfill + jobs).

## Evidence

- `src/tgcli/dispatch.py` (archive sync call)
- `src/tgcli/archive/sync.py`, `backfill.py`
- `src/tgcli/commands/archive.py` (media after backfill)
- Thermos Wave 5 security Important#2–3

## Acceptance

1. Tests: capped sync/backfill stop without unbounded media tail.
2. Full gate green. CONTRACT note if behavior becomes newly explicit.
