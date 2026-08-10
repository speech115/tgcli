## 2026-08-10 — release 2.0.4 integration

**Did:** merged the #169–#175 wave as `main ← #176 ← #177`, bumped 2.0.3 →
2.0.4 with the CHANGELOG section and compare link, trued up five ceilings with
`check-architecture.py --strict` (cli 705, parser 759, preflight 447, dispatch
353, clone/state 409) and their mirror in `tests/test_check_architecture.py`,
and started tracking `.codex/agents/reviewer.toml` beside the Claude adapter.

**Decided:** #178 was merged into `claude/clone-sync-honesty` instead of
`main` — its base was never retargeted after #176 landed. Rather than
reconstruct the chain, the surviving PR was retitled to name both ADRs and
merged as one squash commit; the tree difference against `main` was verified
to be exactly ADR-0082 + ADR-0083 first. `dispatch.py`'s ceiling was carrying
26 lines the grace band had absorbed from earlier work; trued up here and
labelled as not caused by this release.

**Learned:** a squash-merged base makes the dependent branch conflict even
when its content is a superset — GitHub reported `CONFLICTING` on files both
sides touched, because the merge base predates the squash. `git rebase --onto
origin/main <old-base-tip>` drops the duplicated commits and the merge goes
clean; the tree diff before and after the rebase was identical (23 files,
1394 insertions), which is the check worth running before force-pushing.
`git cherry` cannot prove containment across a squash merge — compare the
branch tree against the squash commit instead.

**Live acceptance:** ran the branch against the stuck clone before merging.
The 55.9 MB protected video downloaded in one pass (`8.0 → 55.9/55.9 MB`),
352 messages copied, the comments cursor reached the source tail, zero floods
in 438 progress lines, and a second run was a clean no-op. The cross-run
resume was therefore *not* exercised live — going serial avoided the limit
that made #169 — so it stands on its tests only.

**Next:** #179 (peer-refusal errors outside clone) and #180 (`media download`
resume identity), both deliberately deferred out of this wave.
