---
id: T22
priority: P2
labels: [documentation, ready-for-agent]
slice: Clone
title: "Align CONTRACT refresh commit text with ADR-0083"
---

## Problem

Code and ADR-0083 use `begin_commit`/`finish_commit` for refresh
(retryable `.pending`). CONTRACT refresh subsection still describes
`consume_preview` and “fresh preview after cooldown / not a retried
--commit”.

## Expected

CONTRACT §11 refresh commit language matches ADR-0083 and code.

## Evidence

- `docs/CONTRACT.md` refresh subsection
- ADR-0083, preflight/cli clone refresh path
- Thermos Wave 4 security Medium#6

## Acceptance

1. CONTRACT wording updated; docs gate happy.
2. No behavior change required unless text revealed a real code bug.
