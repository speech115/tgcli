# ADR-0027: CI enforces ruff lint, ruff format, and pyright basic

Date: 2026-07-17
Status: accepted

## Context

CI ran only `pytest` and the coverage matrix. A typo in a rarely executed
branch (an `except` path, an exotic exit-code route) would only surface at
runtime. The maintenance-mode audit (ADR-0026 session) named a lint +
type-check gate the cheapest remaining automatic guard.

Two facts found during implementation shaped the decision:

- The local edit hook already runs `ruff format` (and unused-import fixes)
  on every touched file. Without a repo-wide format baseline, every future
  PR mixes formatter noise into its logic diff — this happened to
  `commands/clone.py` in this very session.
- `pyright` basic mode found 32 errors: one real looseness (a
  `**dict[str, int]` expansion in `session.py` that blinded the checker to
  every `TelegramClient` kwarg), our own over-loose `object` annotations,
  and a tail of Telethon stub gaps.

## Decision

1. Dev dependencies gain `ruff` and `pyright`; CI runs `ruff check`,
   `ruff format --check`, and `pyright` before `pytest`.
2. One-time repo-wide `ruff format` establishes the baseline the local
   hook already pushes toward (48 files reformatted, behavior unchanged,
   full suite green).
3. `pyright` runs in `basic` mode over `src/` only. Tests stay unchecked:
   they monkeypatch and mock Telethon heavily; typing them is cost without
   payoff.
4. Telethon stub gaps are silenced inline with `# type: ignore` plus a
   short reason, never with repo-wide rule disables. Our own annotations
   were fixed rather than silenced (`session.py` explicit kwargs,
   `Any` instead of `object` where attributes are accessed dynamically,
   `cast` after explicit validation).

## Consequences

- CI fails on lint, format drift, or a basic type error before tests run.
- Future diffs carry no formatter noise; the one-time format commit is the
  price, paid once.
- Telethon pin bumps may surface new stub gaps; fix or inline-ignore with
  a reason, per rule 4.
