---
id: T36
priority: Debt
labels: [enhancement, needs-triage]
slice: Governor & jobs
title: "Atomic breadth check-and-touch in governor ledger"
---

## Problem

Breadth budget uses separate `budget_ok` then `touch_peer`. Concurrent
primary + role job can both see remaining==1 and exceed the 100-peer/24h
hedge (ADR-0072 assumption).

## Expected

Atomic check-and-touch (or equivalent pessimistic lock) in the ledger.

## Evidence

- Thermos Wave 3 security High#3
- `ledger.py`, backfill breadth calls

## Acceptance

Owner + possibly ADR amendment. Concurrent test proves ≤ cap.
