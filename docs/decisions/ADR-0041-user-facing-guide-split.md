# ADR-0041: split user-facing documentation out of the engineering docs

Date: 2026-07-24
Status: accepted

## Context

Owner request (2026-07-24), following the wacli review that produced ADR-0040.
`docs/` is currently an engineering tree: PLAN, DEVLOG, ISSUES, PROPOSALS,
MAP, decisions. The only user-facing surfaces are `README.md`, `SKILL.md`
(173 lines, written for agents), and `docs/CONTRACT.md` (1073 lines, written as
versioned law). Nothing sits between "one-line routing table" and "the full
contract".

The comparison case, [wacli](https://wacli.sh), keeps 26 task-shaped pages
under `docs/` — one per command area — and renders them into a site. The pages
are the reusable asset; the site is a thin renderer over them.

This is a documentation-structure decision, not a behaviour change, so
ADR-0026's maintenance gate does not apply. It does change where documentation
lives, which ADR-0007 governs, hence this record.

## Decision

1. **Add `docs/guide/` — task-shaped, user-facing pages, one per command area.**
   22 pages plus an index, grouped Start / Reading / Writing / Data /
   Operations. Each page answers "how do I do X", shows real invocations, and
   quotes JSON shapes from the contract rather than restating them in prose.

2. **Ownership boundaries stay sharp.** Each existing document keeps exactly
   one job, and the guide takes none of them:
   - `docs/CONTRACT.md` remains versioned law and the single source of truth
     for flags, JSON shapes, and exit codes. **The guide cites it; where they
     disagree, the contract wins and the guide is the bug.**
   - `SKILL.md` remains the agent routing table — one line per task, loaded
     into an agent's context. It is deliberately not replaced by the guide;
     an agent wants the table, a human wants the page.
   - `docs/decisions/`, `MAP.md`, `ISSUES.md`, `DEVLOG.md` stay
     engineering-facing and unchanged.
   - `docs/CLONE.md` stays closed history; `docs/guide/clone.md` is the
     current-behaviour page.

3. **No documentation site.** Publishing a site is rejected for now: the
   repository is private, so GitHub Pages needs a paid plan and would publish a
   public site describing a private tool; and the readership is the owner plus
   agents that read markdown straight off disk, for whom HTML is strictly
   worse. Re-entry condition: **the repository becomes public.** The pages
   this ADR creates are exactly the input such a site would need, so the
   deferral costs nothing.

4. **The guide is checkable, not merely written.** Documentation that drifts is
   worse than none, and this repository's house style is to make claims
   machine-verifiable (`FEATURES.md` + `check-coverage.py`). `scripts/check-docs.py`
   walks the live argparse tree and fails closed when a guide page names a flag
   the parser does not have, a `tg <command>` that does not exist, or a
   relative link that does not resolve. It runs in CI alongside the other
   gates.

## Consequences

- There is now a middle layer between the routing table and the contract:
  a human onboarding to a command reads one page, not 1073 lines of law.
- The guide is a second place that describes behaviour, and therefore a second
  place that can drift. Point 2 (contract wins) and point 4 (mechanical check)
  are what keep that acceptable; without both, this ADR would be a mistake.
- Changing a flag or JSON shape now touches three files in one commit —
  the code, `CONTRACT.md`, and the affected guide page. That cost is accepted
  deliberately: it is the price of documentation a human can actually read.
- `README.md`'s documentation table becomes a pointer into `docs/guide/`
  rather than into raw engineering documents.
- If the repository goes public, the site question reopens with the expensive
  half already done.
