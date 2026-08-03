## 2026-08-03 — Two external process reviews, checked and half-adopted (Claude)

**Did:** verified two external assessments of this repo's process (one against
`openai/codex` and `openclaw`, one against seven more repositories), then
landed the surviving half as ADR-0073 plus the record in `docs/PROPOSALS.md`.

**What did not survive.** The first review's central figure — "73 ADRs for
4365 lines" — counts only `src/tgcli/*.py`; `src/**` is 19 975 lines. Both
reviews put a fast/full gate split and a `check-changed` router first, on an
untimed assumption; `./scripts/gate.sh` measures 13.3 s (pytest 7.6 s, 1726
tests). Five further items were re-proposals of what ADR-0058 shipped on
2026-07-26.

**What the measurements show.** 11 of the 33 squash-merged PRs in history
carry no `src/` line; the ADR-0072 campaign spent 4 of its 7 PRs on paper
(`#143` 741 lines, `#149` 458, `#147` 306, `#144` 60). Across `#143`–`#151`:
1601 src / 2412 tests / 2459 docs — 1.51 and 1.54 per `src/` line, against the
July baseline of 2.8 and 2.0. Test volume nearly halved after ADR-0058; prose
volume did not move. `CLAUDE.md:4` also pointed every starting session at
`docs/DEVLOG.md`, closed at 1.2.16.

**Landed (ADR-0073).** Two lanes chosen by a seven-trigger list — contract,
safety, state, pacing, new dependency/module/abstraction, released behavior,
and the enforcement mechanisms: the check scripts, CI, and this contract with
its adapters. Full lane unchanged; small-fix lane
drops the ADR, plan, index row, status edits, and release bookkeeping, and
keeps the reproducing test, gate, independent review, mirror-fix rule, and
atomic writes. Four subtractions: documents ride with their code; plans only
for campaigns of 3+ PRs; ADR only for behavior reachable from a release tag;
devlog per landed slice at ~15 lines.

**Review (PR #154) returned needs-work and was right on three counts.** The
draft cited `#146`/`#145` for the ADR PRs — those are issues; the PRs are
`#143`/`#147`, and the campaign totals were computed over a set that silently
dropped both, giving 1.45/0.98 instead of 1.51/1.54. The stale-DEVLOG fix
missed its own mirror-fix rule: `.cursor/rules/tgcli-agent-contract.mdc` and
`.claude/agents/reviewer.md` carried the same pointer. And the seven-trigger
list was six — changes to `scripts/check-*.py` logic fell outside every
trigger, so loosening the safety net itself would have taken the small lane.
All corrected here, together with the index back-annotations, `README.md`'s
blanket scoped-plan claim, and two `docs/PROPOSALS.md` restatements of it.

**A second pass found two more.** The ADR-0073 index row still said
"six-trigger" while every other surface said seven — invisible to the docs
gate, which counts ranges but does not read prose. And trigger 7 covered the
check scripts but not the prose contract they enforce: a PR could have deleted
the mirror-fix or audit-ordering rule from `AGENTS.md` on the small lane, and
the independent reviewer draws its checklist from that same file. Trigger 7
now names `AGENTS.md` and its adapters explicitly. The pass reproduced all
campaign figures independently from git.

**Evidence:** `./scripts/gate.sh` green — 1726 passed, 9 skipped; coverage OK
23 namespaces; docs gate 26 guide pages, 32 releases, 0 problems. No `src/`
change, no contract change, no release.

**Next:** the automation rows in `docs/PROPOSALS.md` (release script,
generated CLI reference, complexity reset, deprecation registry, CI
aggregator) stay unapproved — revisit only if the process still feels heavy
after a few slices on the new lanes.
