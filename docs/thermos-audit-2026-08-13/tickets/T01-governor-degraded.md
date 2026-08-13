---
id: T01
priority: P0
labels: [bug, ready-for-agent]
slice: Governor & jobs
title: "Governor: refuse or loudly fail when ledger is degraded"
---

## Problem

When `governor.db` cannot be opened, `Ledger.open()` returns an in-memory
fallback with `degraded=True`. Reads fail open (“nothing is cooling”) and
writes are best-effort. Interactive commands, archive backfill, and
`jobs run --lane telegram` then send RPCs without local refuse or
persistent pacing. `tg doctor` sets `governor_degraded: true` but keeps
`ok: true`, so the operator may miss it.

## Expected

Degraded ledger must not silently remove account-wide protection. Prefer
fail-closed for Telegram-lane work (or an unavoidable stderr alert on
every governed run) and make doctor treat degradation as unhealthy unless
an explicit override is documented in CONTRACT.

## Evidence

- `src/tgcli/governor/ledger.py` (`open`, degraded fallback)
- `src/tgcli/governor/gate.py` (refuse path)
- CONTRACT §5.1 doctor fields
- Thermos Wave 3 security finding P0#1

## Acceptance

1. Reproducing test: simulated ledger open failure → governed mutation or
   telegram job does **not** proceed as if clear (exit/policy path asserted).
2. Doctor reports degradation as a hard failure (or CONTRACT is updated
   and tests match the new honesty rule).
3. Full gate green.
