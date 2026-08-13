---
id: T27
priority: Debt
labels: [enhancement, needs-triage]
slice: Safety & mutations
title: "Table-driven preview→commit; remove dead consume_preview"
---

## Problem

Preview→commit orchestration is duplicated across preflight, dispatch,
cli audit, and command modules. `consume_preview` has no production
callers after ADR-0083. Drift/mirror-fix risk for new mutations.

## Expected

One table-driven handshake seam; delete or test-only-wrap
`consume_preview`. Full-lane (safety behavior).

## Evidence

- Thermos Wave 1 quality findings 1–2
- `safety.py`, `preflight.py`, `cli.py`, send/mutate/draft

## Acceptance

Owner + ADR. Behavior-preserving refactor with existing CLI tests green.
