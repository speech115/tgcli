# ADR-0071: Owner-gated development replaces the "maintenance mode" label

Date: 2026-07-31
Status: accepted
Supersedes: [ADR-0026](ADR-0026-maintenance-mode.md) rule 1 only
(rules 2–4 — document routing, the clone chronicle, and the ADR index —
stay in force).

## Context

[ADR-0026](ADR-0026-maintenance-mode.md) declared the project
feature-complete and in "maintenance mode" on 2026-07-17, the day the clone
feature passed its live gates at v1.0.0. Its rule 1 reads "do not add
features"; two weeks later the repository is at v1.2.25 with ADRs up to
0070, and three subsystems that did not exist at v1.0.0 have shipped:
`accounts login` ([ADR-0042](ADR-0042-accounts-login.md)), the FEED stack
([ADR-0060](ADR-0060-clone-state-sqlite-proposal.md),
[ADR-0062](ADR-0062-job-session-role.md),
[ADR-0063](ADR-0063-tg-changes-design.md)), and the local archive
([ADR-0068](ADR-0068-local-archive-store.md)–[ADR-0070](ADR-0070-archive-refresh-scheduling.md)).

What actually held was never the freeze — it was the gate: nothing reached
code without an explicit owner request plus an ADR plus a scoped plan, and
`docs/PROPOSALS.md` still holds an unapproved wishlist as proof. The label
is the part that drifted. A first screen of AGENTS.md that says
"feature-complete, do not add features" next to a changelog of weekly
feature releases is the same documentation-drift failure ADR-0026 itself was
written to fix, and it costs every session a re-negotiation of whether the
gate is open. A rule that has to be explained away is a rule that erodes the
ones next to it.

## Decision

1. The posture is named **owner-gated development**. Its mechanics are
   ADR-0026 rule 1 unchanged, with the freeze claim dropped:
   - A new feature or behavior change needs an **explicit owner request plus
     an ADR and a scoped plan** — never a new phase in `docs/PLAN.md`.
   - **A bug fix starts from a reproducing test**, then the minimal fix.
   - **An agent never widens the scope it was given**; when in doubt whether
     something is a fix or a feature, ask the owner.
2. ADR-0026 rules 2–4 are untouched: `docs/ISSUES.md` plus the ADR index
   carry current scope, the clone chronicle lives in `docs/CLONE.md`, and
   `docs/decisions/README.md` remains the canonical ADR index whose row is
   added in the same commit as its ADR.
3. The wording lands wherever the old label was asserted: the AGENTS.md
   section, `CONTRIBUTING.md` "What lands here", the README status badge and
   its Status/Contributing sections, the `docs/PROPOSALS.md` and
   `docs/ISSUES.md` gate headers, the `docs/PLAN.md` status note, and the
   Cursor adapter rule (renamed to `.cursor/rules/tgcli-agent-contract.mdc`,
   which is what it always contained).

## Rejected alternatives

- **Keep "maintenance mode".** Cheapest, and the mechanics do work — but the
  contradiction between the badge and the changelog recurs in every session,
  and a knowingly false first screen teaches agents to read the contract
  selectively.
- **Drop the gate (open development).** Loses the protection that is doing
  the actual work: an agent extending scope on its own initiative.
- **"Active development"** without a gate word. Accurate about the pace and
  silent about the ADR requirement — exactly the half of the contract that
  must not be missable.
- **Retire ADR-0026 entirely.** Its rules 2–4 are still the source of the
  document routing and the ADR index; a wholesale supersession would orphan
  them.

## Contract impact

None. `docs/CONTRACT.md`, the CLI surface, JSON shapes, and exit codes are
unchanged; this ADR is process wording only and ships without a version
bump or release section (ADR-0038 applies to contract changes).
