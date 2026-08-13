---
id: T33
priority: Debt
labels: [enhancement, needs-triage]
slice: Governor & jobs
title: "JobKind registry + shared lane loop / cooldown deferral"
---

## Problem

Kind dispatch is stringly-typed across arguments/preflight/model/runner.
`run_local` / `run_telegram` duplicate loop skeletons; RateLimit and
FloodWait handlers are copy-paste.

## Expected

`JobKind` registry in `model.py`; `_run_lane_loop` +
`_defer_for_cooldown`. Optionally clear legacy clone cooldown read path
(separate owner call).

## Evidence

- Thermos Wave 3 quality P1/P2
- ADR-0087

## Acceptance

Owner request. Adding a fifth workload touches one registry, not four
files. Tests green.
