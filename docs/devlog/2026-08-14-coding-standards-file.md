## 2026-08-14 — Coding standards file (Composer)

**Did:** Added `CODING_STANDARDS.md` at the repo root — the active, checkable
rule list review enforces, in the Matt Pocock `/code-review` pattern (notice
the agent doing something bad, add a rule, review enforces it from then on).
Wired it into review: `.claude/agents/reviewer.md` reads it before the diff and
its Standards axis checks every rule; `AGENTS.md` Read First item 6 and the
Standards review axis name it. Added a `docs/MAP.md` row.

**Decided:** `AGENTS.md` stays the canonical contract and `docs/CONTRACT.md`
the versioned law; the new file holds only mechanical, one-line rules and a
"how to add a rule" preamble. Rules needing a policy debate stay in AGENTS.md
or an ADR.

**Learned:** The file overlaps `CONTRIBUTING.md`'s working rules by design —
that is the distillation, not a second source of truth.

**Next:** Add rules to `CODING_STANDARDS.md` as review catches new mistakes.
