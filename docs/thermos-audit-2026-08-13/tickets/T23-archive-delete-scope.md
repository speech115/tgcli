---
id: T23
priority: P2
labels: [bug, ready-for-agent]
slice: Archive
title: "Scope-gate channel deletes in archive sync"
---

## Problem

Peer-scoped `message_delete` applies tombstones without checking archive
scope. Combined with T09 (removed-but-still-subscribed channels), deletes
mutate tombstones after leave.

## Expected

Channel deletes respect `_in_archive_scope` / equivalent. Prefer same
PR as T09.

## Evidence

- `src/tgcli/archive/sync.py` (`apply_events` delete branch)
- Thermos Wave 5 security Important#4

## Acceptance

1. Test: out-of-scope channel delete does not insert tombstones.
2. Full gate green.
