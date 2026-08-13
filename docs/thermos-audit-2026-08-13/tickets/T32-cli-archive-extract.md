---
id: T32
priority: Debt
labels: [enhancement, needs-triage]
slice: CLI shell
title: "Extract archive parser/preflight; pull offline routing out of cli.py"
---

## Problem

`cli.py` (747) and `parser.py` (726) sit on ceilings. `_execute` grew
back into an offline router. Archive validation is duplicated with
`jobs/preflight`. Jobs-style extraction is the proven pattern.

## Expected

`archive/arguments.py` + `archive/preflight.py`; offline executor module;
cli returns to lifecycle-only. Shared validate specs for CLI and jobs.

## Evidence

- Thermos Wave 8 quality P0/P1
- ADR-0035 intent

## Acceptance

Owner request. Ceilings drop or gain headroom; tests green.
