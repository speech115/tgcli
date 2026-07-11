# ADR-0011: Fail closed when the mutation audit cannot be written

Status: accepted (2026-07-10)

## Context

Before a send or authorised raw API write reaches the network, `append_audit`
records the action in the local JSONL audit log. An `OSError` from its parent
directory, open, or write path used to escape as an unstructured traceback.

Continuing after that failure would create a mutation with no audit record,
which violates the Phase 4 safety model. Reporting a raw traceback also breaks
the CLI error contract.

## Decision

Audit persistence is fail-closed. `append_audit` converts an `OSError` into a
`PolicyError`, so the command exits 2 and never dispatches the mutation.

## Consequences

- Disk-full and permission failures are structured `BLOCKED` CLI errors.
- Operators must restore the local audit path before authorised writes resume.
- A successful audit record remains a precondition for both `tg send --commit`
  and `tg api --write`.
