## 2026-08-09 — integrating the six-PR review slice and releasing 2.0.3

**Did:** merged #165, #168, #164, #167, #163, #166 into `main` and prepared
release 2.0.3 (`scripts/prepare-release.py`, CHANGELOG prose, `uv.lock`
refresh for the version bump — the same lag 2.0.1 hit). The repository
squash-merges, so the stacked branches could not simply be merged in order:
after #167 landed, #163's own commit was replayed with
`git rebase --onto main` and force-pushed, and #166's two commits were
replayed onto the new `main` the same way. Independent PRs (#164/#165/#168)
needed no rebase.

**Decided:** merge order #165/#168/#164 → #167 → #163 → #166, verifying
`main` after each step (transcribe test count, ADR index shape, full suite).
Rebase-per-step rather than merge commits, to keep the linear history the
repository has used since 2.0.0.

**Learned:** a pre-merge review that reports status as a table of ticks hides
identity errors behind counts. Three claims in the incoming report were wrong
and only structural checks caught them: a "restored" test file matched on
count (9) while a named regression test was still missing; a claimed rebase
had never been pushed (`merge-base --is-ancestor` disproved it); a "repo-wide"
doc fix had left `docs/guide/transcribe.md` contradicting `docs/CONTRACT.md`
on the same JSON shape. `tests/test_check_docs.py` validates ADR index ranges
and counts but not table well-formedness, which is how two rows survived with
empty description cells and orphaned text lines.

**Next:** three ADR-0079 gate cases remain uncovered on `main` — an audit
record on timeout, the `TGCLI_READONLY` environment path for `tg transcribe`,
and the absence of an audit record when the gate blocks. A reconstructed test
file for them is staged outside the repository; land it in a small-fix lane
PR.
