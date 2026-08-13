---
id: T35
priority: Debt
labels: [enhancement, needs-triage]
slice: Read path
title: "Decompose changes.py poll/wait; add architecture ceiling"
---

## Problem

`commands/changes.py` is 513 lines with duplicated difference walks and
wait/settle loops, and no CEILINGS entry. `read_ops` is already at its
ceiling; entity resolve is still copied across read commands.

## Expected

Collapse poll/wait helpers inside changes; add ceiling. Optional follow-
on: canonical `resolve_entity` + message row projection for
read/search/thread.

## Evidence

- Thermos Wave 7 quality

## Acceptance

Owner request. Ceiling present; duplication reduced; tests green.
