---
id: T12
priority: P1
labels: [bug, ready-for-agent]
slice: Clone
title: "Delete reupload cache only after confirm + state save"
---

## Problem

`_reupload_batch` `rmtree`s the download cache after Telegram accepts
the send, before `confirmed_destination_ids` and `state.save`. If
confirmation is incomplete or the process dies before save, hundreds of
MB are gone and the next run re-downloads (ADR-0052 intent violated).

## Expected

Delete cache only after successful confirm + durable mapping save.
FloodWait before return must still keep the cache (already true).

## Evidence

- `src/tgcli/commands/clone.py` (`_reupload_batch`, `_forward_batch`)
- ADR-0052
- Thermos Wave 4 security Medium#4

## Acceptance

1. Test: failure between send and save leaves cache intact.
2. Success path still clears cache.
3. Full gate green.
