## 2026-08-03 — External process reviews, checked against measurements (Claude)

**Did:** reviewed two external assessments of the tgcli development process
(one comparing it to `openai/codex` and `openclaw`, one assessing that review
plus seven more repositories), verified their claims — about the external
repositories and about this one — and landed the surviving items as a new
section in `docs/PROPOSALS.md`. No code, no contract change, no owner gate
crossed: the section is a backlog draft under ADR-0071.

**What did not survive.** The first review's central figure, "73 ADRs for 4365
lines of code", counts only `src/tgcli/*.py`; `src/**` is 19 975 lines, so the
ratio it argues from is off by 4.6×. Its headline recommendation — move the
gate from commit to push — and the second review's `check-changed` lane router
both assume the gate is expensive. It is not: `./scripts/gate.sh` measured
13.3 s wall clock, pytest 7.6 s for 1726 tests under `-n auto`. The second
review states in its own critique that the gate was never timed and that no
speed-up claim is honest without that number; it then built its P0 on the
untimed assumption anyway. Five further items (ADR-lite, per-file devlog,
integrator-owned CHANGELOG/version, ceiling grace band, `pytest -n auto`) are
re-proposals of what ADR-0058 already shipped on 2026-07-26.

**What the measurements do show.** 11 of 33 squash-merged PRs carry no `src/`
line; the ADR-0072 campaign spent 4 of its 8 PRs on paper alone (`#146` 741
lines, `#149` 458, `#145` 306, `#144` 60). The cost is serialization — one
branch/gate/review/merge cycle per document — rather than the documents. A
concrete instruction drift also turned up: `CLAUDE.md:4` points a starting
session at "the tail of `docs/DEVLOG.md`", which `AGENTS.md:57` declares
closed at `1.2.16`, 35 releases back.

**ADR-0058 is working.** The 2026-07-26 baseline was ≈2.8 test lines and ≈2.0
docs lines per `src/` line. Across `#144`–`#151`: 1601 src / 2326 tests / 1561
docs — 1.45 and 0.98. Overhead halved with no new rule, which is itself an
argument against adding rules to chase it further.

**Landed.** `docs/PROPOSALS.md` gains "Process-speed rule revisions, round 2
(2026-08-03)": seven rows, ordered by value — fix the `CLAUDE.md` entry point;
documents ride with their code; the complexity-reset rule from
`openai-agents-python`; `scripts/prepare-release.py`; generating the CLI
reference and contract tables from `build_parser()` (ruff's `generate-all`,
cli/cli's generated man pages); a deprecation registry with version deadlines
(mise's `deprecated_at!`), to adopt at the first real deprecation; and codex's
`always()` CI aggregator. Every external claim in the section was verified
against the source repository rather than taken from the reviews.

**Not adopted, recorded so it is not re-proposed:** the fast/full gate split,
a `check-changed` lane router, fixed PR line limits, and the openclaw
lane/bot/Testbox infrastructure — the last being heavier than what we run, not
lighter.

**Then the owner approved the cheap half, and it landed as ADR-0073.** Two
lanes selected by a fixed six-trigger list (contract, safety, state, pacing,
new dependency/module/abstraction, released behavior): the full lane is
unchanged, the small-fix lane drops the ADR, the plan, the index row, the
status edits, and the release bookkeeping while keeping the reproducing test,
the gate, the independent review, the mirror-fix rule, and atomic writes.
Plus four subtractions: documents ride with their code (no paper-only PRs),
plans only for campaigns of 3+ PRs, ADR only for behavior reachable from a
release tag, devlog per landed slice at ~15 lines. `CLAUDE.md:4` now points at
`docs/devlog/` instead of the closed log. The lane list is deliberately a list
and not a judgment call — an ambiguous change takes the full lane.

**Evidence:** `./scripts/gate.sh` green — 1726 passed, 9 skipped in 7.97 s;
coverage OK 23 namespaces; docs gate 26 guide pages, 32 releases, 0 problems.
The MAP ADR-range counter and its `tests/test_check_docs.py` mirror moved to
0073. No `src/` change, no contract change, no release.

**Next:** the automation rows in `docs/PROPOSALS.md` stay unapproved —
revisit only if the process still feels heavy after a few slices on the new
lanes.
