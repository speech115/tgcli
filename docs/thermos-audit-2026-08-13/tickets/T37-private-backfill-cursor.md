---
id: T37
priority: Debt
labels: [enhancement, needs-triage]
slice: Governor & jobs
title: "Private archive-backfill job: cursored dialog enumeration"
---

## Problem

`--private` job quanta call `enumerate_private_dialogs` from the start
of `iter_dialogs` each time, O(n) RPC per quantum on large accounts,
burning pacing/breadth/wall-clock without progress.

## Expected

Durable enumeration cursor / resume token across quanta.

## Evidence

- `src/tgcli/commands/archive_jobs.py`, `archive/backfill.py`
- Thermos Wave 3 security High#6

## Acceptance

Owner request. Test: second quantum does not re-walk completed head.
Full gate green.
