# ADR-0120: Process reset — rules live in checks, history lives in git

Date: 2026-10-05
Status: accepted
Supersedes: ADR-0007 (MAP and DEVLOG duties), ADR-0058 decisions 1–2
(integrator releases, per-session devlog), ADR-0065 (MAP inventory and devlog
routing checks), ADR-0038 rule 3 (a release per contract change), ADR-0073
(change lanes), ADR-0074 rule 1 (complexity reset), ADR-0026 rules 2–3 and
ADR-0071 decision 2 (ISSUES and CLONE routing), ADR-0065's status-closure
duty, ADR-0058 decision 3 and ADR-0110 decision 1 (ceiling grace and
`--strict`); amends ADR-0071 (an ADR only for decisions that are hard to reverse)

## Context

By 2026-08 the repository carried about 45k lines of documentation against
23.5k lines of source. The agent-facing rules alone spanned about 2.5k lines
in 16 files, and the same rule was restated in up to seven of them. About 29k
lines were closed history (completed plans, two closed devlogs, 168 session
entries, audit tickets) that agents still grepped. The work stalled on
2026-08-14 with the gate green. The cost was in the paperwork, not the code.

## Decision

A rule stays only if a check enforces it or it needs judgment. A rule a
check already enforces loses its prose; a rule nothing enforces either gets a
check or goes.

1. Closed history is deleted from the tree; git history keeps it.
   `docs/devlog/`, `DEVLOG*.md`, `PLAN.md`, `CLONE.md`, `superpowers/`, the
   thermos audit with its publisher (`scripts/publish-thermos-backlog.py` and
   its `issues:write` workflow), handoffs, research, and plans go.
2. `docs/MAP.md` goes: a hand-synced inventory of the tree that the tree
   already is.
3. `docs/ISSUES.md` and `docs/PROPOSALS.md` go. Open items move to GitHub
   issue #286, in the tracker `docs/agents/issue-tracker.md` already names.
4. `scripts/check-docs.py` keeps only checks of docs against the real CLI
   (guide flags, commands, links, README surface, benchmark claims). The
   process-bookkeeping checks go: MAP inventory, devlog routing, ADR index
   rows, ADR supersession headers, CHANGELOG link definitions.
5. Adapters stop restating the contract. `CLAUDE.md` imports `AGENTS.md`,
   `.cursor/rules/` goes (Cursor reads `AGENTS.md`), and `CONTRIBUTING.md`
   and the PR template shrink to pointers.
6. Commit messages, PR descriptions, and issues are the session record.

7. `AGENTS.md` becomes two lists: rules paired with the check that enforces
   them, and a short list of judgment rules. `CODING_STANDARDS.md` merges
   into it. When the owner corrects an agent, the fix goes to architecture,
   then a lint or test, and only last to the judgment list.
8. Change lanes, the integrator role, and a release per contract change go.
   A release happens when the owner asks, through `scripts/prepare-release.py`
   and the `Release tag` workflow. An ADR is written only for a decision that
   is hard to reverse.
9. "stdout carries contract data only" gains a check: ruff `T20` bans `print`
   in `src/`.
10. `scripts/check-architecture.py` drops the +50 grace band and `--strict`:
    with no integrator, a ceiling is a hard limit on every run, and the PR
    that grows a file past it raises the number.

## Rejected alternatives

- **Move history to `docs/archive/`.** Agents still grep it, and git
  already archives it.
- **Keep the devlog, shorter.** It duplicates commit messages and PR bodies.
  Every enforced duty around it existed only to keep it in sync.

## Contract impact

None. No CLI flag, JSON shape, exit code, or safety behavior changes.
