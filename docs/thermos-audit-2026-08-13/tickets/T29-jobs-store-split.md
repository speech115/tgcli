---
id: T29
priority: Debt
labels: [enhancement, needs-triage]
slice: Governor & jobs
title: "Split jobs/store.py before the next jobs feature"
---

## Problem

`jobs/store.py` is 919 lines = exact ceiling. Bootstrap, CRUD, state
machine, and lane lock share one file. Next feature forces integrator
ratchet.

## Expected

Split bootstrap (`jobs/db.py`) from transitions; move retry policy
constants toward `model.py`. Do this before the next jobs product slice.

## Evidence

- Thermos Wave 3 quality P1
- `scripts/check-architecture.py`

## Acceptance

Owner request. Ceiling headroom restored; tests green.
