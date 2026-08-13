---
id: T21
priority: P2
labels: [bug, needs-triage]
slice: Gates & contract surface
title: "Make incremental export append crash-safe or document limit"
---

## Problem

Full export uses atomic temp+replace. `--append`/`--resume` writes
in-place; mid-run failure leaves a torn file. CONTRACT §7 only
guarantees atomicity without append.

## Expected

Owner choice: sidecar checkpoint / temp+rename for append batches, or
explicit CONTRACT caveat + detection of partial runs. Triage first.

## Evidence

- `src/tgcli/commands/export.py`
- CONTRACT §7
- Thermos Wave 9 security Medium#3

## Acceptance

1. Policy chosen and locked by tests or CONTRACT wording.
2. Full gate green.
