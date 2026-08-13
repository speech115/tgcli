# ADR-0108: Table-driven preview-to-commit handshake

Date: 2026-08-13
Status: accepted
Form: full (safety behavior)
Owner request: thermos debt T27

## Context

The eight preview-to-commit operations (`send`, `edit`, `delete`, `forward`,
`draft set`, `draft clear`, `clone init`, and `clone refresh`) share one
safety protocol but previously described it independently in three lifecycle
modules. `preflight.py` selected the expected preview kind and loaded its
payload, `dispatch.py` selected preview versus commit callables, and `cli.py`
selected the before/result audit records and the point at which a successful
commit becomes `.used`. Adding a mutation therefore required synchronized
conditionals across those files. A missed branch could weaken kind binding,
audit ordering, or retryability.

`safety.consume_preview` was the older burn-before-work state transition.
ADR-0083 moved the last production callers to the retryable
`begin_commit`/`finish_commit` protocol, but the dead function and tests for it
remained. Keeping two commit protocols exposed the unsafe one as an apparent
choice.

## Decision

1. Add `tgcli.preview_commit` as the single orchestration seam. Its
   `HANDSHAKES` registry has one descriptor per operation and owns:
   - command/subcommand selection and expected persisted `kind`;
   - preview argument requirements, commit-only argument rejection, readonly
     gating, `begin_commit`, and clone source binding;
   - preview and commit callables plus row projection;
   - pre-mutation and result-audit details; and
   - `finish_commit` after all required result audit work succeeds.
2. The command modules continue to own operation-specific payload creation,
   Telegram request construction, stale-state checks, and result shapes. The
   registry coordinates those existing functions; it does not generalize
   their domain behavior.
3. Preserve the existing failure order. Commit argument misuse fails before
   mutation gates; mutation gates fail before preview loading; kind/source
   mismatch fails before config or session work; a mutation or result-audit
   failure leaves `.pending` retryable until the original TTL; only a
   successful lifecycle finishes the preview.
4. Clone init/refresh retain their command-owned per-mutation audit records.
   The registry adds no wrapper audit for them, but it owns their final
   `.pending` to `.used` transition.
5. Delete `safety.consume_preview`. `create_preview`, `begin_commit`, and
   `finish_commit` are the only production preview state transitions.

## Safety invariants

- Preview runs remain allowed under `--readonly`, `TGCLI_READONLY=1`, and
  `TGCLI_NO_SEND=1`; commit runs remain blocked by all three.
- A preview kind can authorize only its registered operation.
- Audit persistence remains fail-closed before Telegram writes and before a
  successful result can spend the preview.
- Message `random_id` values stay in the before-audit when present, so retries
  retain their existing idempotency evidence.
- The refactor changes no Telegram request or input type.

## Rejected alternatives

- **Keep lifecycle conditionals and only add a shared kind table.** Dispatch,
  audit, and finish timing could still drift independently, which is the debt
  T27 asks to remove.
- **Put the registry in `safety.py`.** Importing command callables there would
  make the state-storage module depend on its consumers and create circular
  imports, while leaving storage and orchestration with unrelated reasons to
  change.
- **Retain `consume_preview` as a compatibility alias.** It has no production
  caller or public contract, and its burn-before-work semantics are precisely
  what ADR-0083 replaced.
- **Move payload and Telegram request logic into the registry.** That would
  erase useful command-module boundaries and turn a routing table into a
  second command implementation.

## Verification

The registry has a structural coverage test naming all eight handshakes. The
existing public CLI suites remain the behavior oracle for argument misuse,
readonly gates, kind/source mismatch, exact Telegram requests, audit order,
pending retries, and successful finish transitions. No live Telegram smoke is
needed because no request construction or server-facing behavior changes.

## Contract impact

None. CLI flags, JSON shapes, stdout/stderr, exit codes, preview TTL, audit
records, and retry behavior are unchanged. `docs/CONTRACT.md`, versions, and
`CHANGELOG.md` are untouched.
