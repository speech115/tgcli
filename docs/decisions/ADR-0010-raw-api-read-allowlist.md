# ADR-0010: Explicit phase-2 raw API read allowlist

Status: accepted (2026-07-10)

Supersedes: ADR-0008's phase-2 read-classification rule

## Context

ADR-0008 allowed raw API calls from method-name prefixes (`get*`, `search*`,
`check*`, and `resolve*`). Telegram TL method names do not prove an operation
is safe: `auth.checkPassword` checks a password and `account.getTmpPassword`
creates a credential. Both would pass the prefix rule before configuration,
session, and network safeguards could apply.

## Decision

Phase 2 uses a reviewed, explicit, default-deny allowlist. The initial
allowlist contains only `users.getFullUser`, which is required by Phase 2
acceptance. Every method not listed exits 2 before configuration loading,
session acquisition, or network dispatch.

New raw API methods require an ADR-0010 update and a regression test proving
the exact method reaches the dispatcher. Method names and namespaces are never
used as evidence that an operation is read-only.

## Consequences

- `auth.checkPassword` and `account.getTmpPassword` are blocked despite their
  read-looking names.
- Phase 2 intentionally exposes a narrow raw surface; additional use cases
  are reviewed one method at a time.
- ADR-0008 continues to govern the passthrough rationale and the future
  phase-4 write-path requirements.
