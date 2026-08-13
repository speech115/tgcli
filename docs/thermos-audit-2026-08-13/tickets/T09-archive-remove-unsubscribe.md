---
id: T09
priority: P1
labels: [bug, ready-for-agent]
slice: Archive
title: "archive remove must drop channel from changes cursor"
---

## Problem

`archive remove` drops scope only. The peer can remain in the persisted
changes cursor, so later `archive sync` still calls
`GetChannelDifference`. Events are mostly skipped by scope gates, but
deletes may still tombstone (T23) and the account keeps paying RPC cost.

## Expected

Remove also drops the channel from the account changes cursor (or
equivalent unsubscribe). Prefer shipping T23 in the same slice.

## Evidence

- `src/tgcli/commands/archive.py` (`remove_chat`)
- `src/tgcli/archive/sync.py` (`_ensure_channel_subscriptions`)
- CONTRACT §13 / ADR-0068
- Thermos Wave 5 security Important#1

## Acceptance

1. Test: after remove, sync no longer polls that channel.
2. Full gate green.
