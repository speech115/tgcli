# ADR-0073: Risk-tiered change process — a small-fix lane and four ceremony subtractions

Date: 2026-08-03
Status: accepted
Amends: [ADR-0071](ADR-0071-owner-gated-development.md) rule 1 mechanics
(the gate itself stands), [ADR-0058](ADR-0058-process-speed-revisions.md)
documentation cadence, and the ADR trigger from
[ADR-0007](ADR-0007-docs-discipline.md).
[ADR-0038](ADR-0038-versioned-releases-changelog.md) release policy is
untouched.

## Context

The owner reports that small changes cost as much as large ones. The
measurements agree. Every change carries a fixed toll regardless of size: an
8-line `src/` fix (PR #89) landed 59 test lines, 47 docs lines and touched
10 files; a 27-line change (PR #99) touched 28 files with 182 docs lines.
Ceremony also travels in its own PRs — 11 of 33 squash-merged PRs contain no
`src/` line at all, and the ADR-0072 campaign spent 4 of its 7 PRs on paper
alone (#143 draft ADR 741 lines, #149 plans 458, #147 acceptance 306, #144
devlog 60). A large feature amortizes that toll; a one-line bug fix pays it
in full.

What the toll is *not* is verification cost. `./scripts/gate.sh` runs in
13.3 s wall clock (pytest 7.6 s, 1726 tests under `-n auto`), and the July
2026 measurement put PR cycle time at a 1.2 h median. Two external process
reviews (recorded in `docs/PROPOSALS.md`, 2026-08-03) both proposed splitting
the gate into fast and full tiers behind a changed-file lane router; at 13 s
that router costs more than it saves and adds a defect class this repo does
not have — a check that failed to be selected. Neither review is adopted on
that point.

The real defect in the process is that it does not distinguish risk. This
tool mutates a live Telegram account: it deletes and edits real messages,
clones channels, and holds session material. The discipline exists for
those paths, and its hit rate is documented (ADR-0058: 4 of 5 hardening
slices returned needs-work; the same campaign measurement, recorded in
`docs/PROPOSALS.md`, counted 13 of 133 commits as review fixes). But the
same ceremony currently applies to a stderr wording fix. Rules written for
the dangerous half are being paid for by the safe half.

## Decision

**1. Two lanes, selected by a fixed list — not by judgment.** A change takes
the **full lane** when it touches any of:

- `docs/CONTRACT.md` semantics — CLI flags, JSON shapes, exit codes;
- safety behavior — preview→commit, readonly gates, audit records, or any
  mutation path (send, edit, delete, forward, mark-read, clone writes);
- session, config, or persistent state files, including their schemas;
- request pacing and FloodWait handling (ADR-0072);
- a new dependency, a new module, or a new abstraction (the YAGNI rule);
- what a **released** command does, as reachable from a release tag;
- the enforcement mechanisms themselves — the logic of `scripts/gate.sh`,
  `check-architecture.py`, `check-docs.py`, `check-coverage.py`, or the CI
  workflows. These make every other rule bite, so loosening one is a safety
  change even when no product behavior moves. (Line-ceiling *numbers* stay
  integrator-owned under ADR-0058 and are not a full-lane trigger by
  themselves.)

Everything else takes the **small-fix lane**: no ADR, no scoped plan, no ADR
index row, no PROPOSALS/ISSUES status edit, no release bookkeeping. What the
small lane keeps, without exception: the reproducing test first, the full
gate, the independent whole-diff review, the mirror-fix rule, and atomic
state writes. When a change is ambiguous, it takes the full lane; when it is
ambiguous whether it is a fix or a feature, ADR-0071 still says ask the
owner.

**2. Documents ride with their code.** An ADR, plan, or devlog entry lands in
the PR that carries its implementation. A document-only PR is reserved for a
decision deliberately taken before the work is scoped — an ADR proposed for
owner review, or a campaign plan spanning several PRs — not as the default
shape.

**3. Plans only for campaigns.** A scoped plan document is required when a
change spans three or more PRs or introduces a subsystem. Below that, the
ADR's own decision section is the plan.

**4. ADR only for released behavior.** Compatibility begins at a release tag.
An implementation that has not shipped is replaceable and is not a sunk
cost: reworking unreleased code needs no superseding ADR, only the amended
one. The ADR trigger stays as written for the full-lane list above.

**5. Devlog per slice, not per session, and short.** One entry per landed
slice or per session that produced a decision, incident, or handoff worth
carrying — target 15 lines, facts over narrative. A session that produced
nothing landable adds no entry. `docs/DEVLOG.md` and `docs/DEVLOG-v1.md`
remain closed.

## Rejected alternatives

- **Fast/full gate split and a changed-file lane router** (proposed by both
  external reviews, modelled on openclaw's `check:changed`): saves at most
  13 s, costs a permanent piece of infrastructure, and can silently skip a
  check. Rejected on the measurement.
- **Fixed PR line limits** (codex uses 800/500): addresses diff size, which
  is not the measured failure mode here; prose volume is.
- **Weakening TDD, boundary tests, the independent review, or the gate**:
  ADR-0058 already measured their hit rate. Unchanged.
- **Dropping the devlog**: it is the memory that survives between agents and
  hosts. Reduced in cadence and length, not removed.
- **Adopting openclaw's lane/bot/Testbox machinery**: heavier than what this
  repo runs, not lighter; it serves a monorepo with a team.

## Contract impact

None. No CLI flag, JSON shape, or exit code changes; `docs/CONTRACT.md` is
untouched and no release is required (ADR-0038 rule 1 does not trigger).
This ADR changes process rules only, and lands as an `AGENTS.md` edit in the
same PR.
