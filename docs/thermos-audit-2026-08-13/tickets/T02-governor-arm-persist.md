---
id: T02
priority: P0
labels: [bug, ready-for-agent]
slice: Governor & jobs
title: "Governor: fail closed when FloodWait arm cannot persist"
---

## Problem

After Telegram returns FLOOD_WAIT, `arm_from_flood` / `arm_cooldown` may
fail to commit (SQLite error, busy timeout, disk). The FloodWait is still
re-raised, but the next process/quantum sees no cooldown in the ledger
and can deepen the penalty.

## Expected

Failed arm after a server flood must not fail open. Retry the write, or
refuse further RPCs of that type until arm succeeds, with a clear
PolicyError / RateLimitError path.

## Evidence

- `src/tgcli/governor/ledger.py` (`arm_cooldown`, `reserve`)
- `src/tgcli/governor/gate.py` (flood path)
- Thermos Wave 3 security finding P0#2

## Acceptance

1. Test forces arm write failure after FloodWait → subsequent same-type
   request is still refused (or process exits with explicit policy error).
2. Full gate green.
