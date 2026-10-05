# ADR-0120: Process reset — rules live in checks, history lives in git

Date: 2026-10-05
Status: accepted
Supersedes: ADR-0007 (MAP and DEVLOG duties), ADR-0058 decision 2 (per-session
devlog), ADR-0065 (MAP inventory and devlog routing checks)

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

`AGENTS.md` itself is rewritten in a follow-up as a table that pairs each rule
with what enforces it.

## Rejected alternatives

- **Move history to `docs/archive/`.** Agents still grep it, and git
  already archives it.
- **Keep the devlog, shorter.** It duplicates commit messages and PR bodies.
  Every enforced duty around it existed only to keep it in sync.

## Contract impact

None. No CLI flag, JSON shape, exit code, or safety behavior changes.
