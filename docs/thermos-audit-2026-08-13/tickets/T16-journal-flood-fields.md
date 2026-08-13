---
id: T16
priority: P2
labels: [bug, ready-for-agent]
slice: CLI shell
title: "Journal flood fields only on flood exits"
---

## Problem

On any nonzero exit, journal may attach `pacing.last_stop()` flood
metadata even when the final error is NOT_FOUND/CONFIG/TIMEOUT after a
survived flood. CONTRACT §9 ties those fields to flood-related exits.

## Expected

Attach `last_stop()` only when `exit_code == 5` or `error_code ==
FLOOD_WAIT`.

## Evidence

- `src/tgcli/cli.py` (journal stop_fields)
- CONTRACT §9
- Thermos Wave 8 security Medium#1

## Acceptance

1. Test: survived flood then NOT_FOUND → journal without retry_after.
2. Flood exit 5 still records fields.
3. Full gate green.
