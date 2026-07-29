# ADR-0065: Active documentation drift gates

Date: 2026-07-29
Status: accepted

## Context

An owner-requested documentation audit found that the canonical
`docs/CONTRACT.md` and task guide matched the CLI closely, while active
summaries outside the ADR-0041 gate had drifted: README omitted
`--session-role` and `tg changes`, overstated `random_id` retry coverage,
and called the representative live benchmark exhaustive; MAP carried stale
guide/ADR counts; ISSUES and PROPOSALS had not closed shipped work; and the
contributor workflow still routed session entries to the ADR-0058-closed
`docs/DEVLOG.md`. The existing gate checked only guide claims, links, and
CHANGELOG release definitions, so all of those defects passed CI.

## Decision

Extend `scripts/check-docs.py` and its public subprocess tests with the
mechanically derivable active-document invariants:

1. README's global-flags summary covers every root long option from
   `build_parser()`.
2. Every task page under `docs/guide/` is linked from README.
3. README cannot use the two known broad phrasings that apply Telegram
   `random_id` confirmation to preview kinds that do not carry one.
4. Documentation may call `scripts/bench.py` exhaustive only when its static
   command invocations cover every top-level parser command.
5. MAP's guide count and final ADR number match the tree.
6. Contributor docs route session entries to `docs/devlog/`, never the closed
   `docs/DEVLOG.md`.

Keep semantic status closure as an explicit workflow duty: when work
graduates or ships, the same slice updates README/SKILL where public,
ISSUES/PROPOSALS status, MAP where structural, and the per-session devlog.
The PR template and contributor/agent contracts name that checklist.

Implementation scope is one tooling/docs slice: regression tests first,
minimal gate expansion, reconciliation of the confirmed stale documents,
then the full repository gate. No CLI behavior or release metadata changes.

## Rejected alternatives

- Treat the audit as a one-off copy edit: rejected because the same
  mechanically detectable classes would remain able to pass CI.
- Compare every prose sentence with implementation automatically: rejected
  as false confidence; JSON and safety semantics remain owned by CONTRACT,
  boundary tests, and review.
- Duplicate the full CONTRACT in README: rejected because shorter summaries
  should link to versioned law when command-specific detail would drift.

## Contract impact

None. No CLI flags, JSON/TSV shapes, exit codes, safety behavior, dependencies,
or release version change.
