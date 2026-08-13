---
id: T15
priority: P2
labels: [bug, ready-for-agent]
slice: Read path
title: "Batch JSONL: coerce bools/ints like CLI (reject string false)"
---

## Problem

Batch ops use truthiness / `bool()` on JSON fields. `"false"` and `1`
become True for `search.all`, `contacts.search.global`, `info.full`,
etc. Numeric fields accept `True`→1; some ids skip int validation.
Mis-typed agent JSONL triggers unexpected network calls.

## Expected

Strict JSON types matching CLI argparse semantics. Invalid types →
exit 2 `BLOCKED` at parse time, not RUNTIME mid-run.

## Evidence

- `src/tgcli/read_ops.py` (`from_batch`)
- Thermos Wave 7 security Medium#2–3

## Acceptance

1. Tests for `"false"` / wrong types on each affected flag.
2. Full gate green.
