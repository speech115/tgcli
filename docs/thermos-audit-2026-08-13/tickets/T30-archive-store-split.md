---
id: T30
priority: Debt
labels: [enhancement, needs-triage]
slice: Archive
title: "Split archive/store.py; one peer-identity read seam"
---

## Problem

`archive/store.py` is 1017 lines (hard ceiling) mixing schema,
messages/FTS, transcripts, scope, sync_state. Dual identity in scope ⊕
sync_state forces COALESCE sprawl in explore/search.

## Expected

Decompose persistence modules; one `peers.resolve` / identity read seam.
Follow with ingest shared pipeline (backfill ↔ catch-up) as a later
ticket if needed.

## Evidence

- Thermos Wave 5 quality P0
- ADR-0069 intent

## Acceptance

Owner + likely ADR. Ceiling headroom; behavior-preserving tests.
