## 2026-07-31 — ADR-0071: "maintenance mode" renamed to owner-gated development (Claude Opus 5)

**Did:** owner asked what maintenance mode means and whether it is still
needed. Answer: the gate is needed, the label is not — since ADR-0026
(2026-07-17) the repo went 1.0.0 → 1.2.25 with ADRs 0026 → 0070 and three
new subsystems (`accounts login`, FEED, archive), so "feature-complete, do
not add features" was never applied literally. Wrote
`docs/decisions/ADR-0071-owner-gated-development.md` (ADR-lite) superseding
ADR-0026 **rule 1 only**; rules 2–4 (scope routing, clone chronicle, ADR
index) stay live, which is why ADR-0026 keeps `accepted` with a scoped
supersession note in its header, the index row, and the index's supersession
notes. Rewording landed everywhere the old label was asserted: AGENTS.md
section (now "Owner-Gated Development", with an explicit *never widen the
scope you were given* bullet), CONTRIBUTING "What lands here", README status
badge + Status + Contributing, PROPOSALS/ISSUES gate headers, PLAN status
note, MAP rows, `.github/ISSUE_TEMPLATE/proposal.yml`,
`.github/PULL_REQUEST_TEMPLATE.md`, and the Cursor adapter renamed
`.cursor/rules/tgcli-maintenance.mdc` → `tgcli-agent-contract.mdc` (its
content never was about maintenance mode). The MAP inventory gate's fixture
in `tests/test_check_docs.py` moved 0070 → 0071 with the index. Gate green:
1635 passed, 9 skipped; coverage 23 namespaces; docs problems: 0.

**Decided:** mechanics unchanged, on purpose — owner request + ADR + scoped
plan for behavior, reproducing test first for fixes. No CONTRACT.md change,
so no version bump and no CHANGELOG section (ADR-0038 applies to contract
changes only). Historical ADRs that reference "maintenance mode" in their
Context (0027–0057) are left alone: they were true when written, and closed
history (DEVLOG, DEVLOG-v1, CHANGELOG, superpowers) is never edited.

**Learned:** the drift ADR-0026 was written to prevent had reappeared in
ADR-0026 itself — a first screen claiming a freeze next to a changelog of
weekly feature releases. A posture label ages faster than the rules it
carries, which is an argument for naming postures after their mechanism
(who gates) rather than their moment (what phase we are in).

**Reviewed:** whole-diff Spec + Standards pass from the merge-base, three
findings fixed on this branch. (1) ADR-0071 rule 3 listed fewer surfaces
than the diff touched — the two `.github` templates were missing, the exact
ADR-vs-tree drift this ADR exists to prevent. (2) ADR-0056 items 4–5
*prescribe* the old wording ("the ADR-0026 gate up front", a "maintenance
status" badge) and stayed `accepted`, so a later session could have restored
the label from a live rule source; both now carry a scoped amendment note in
the ADR header and the index row, following the ADR-0038/0058 precedent.
(3) ADR-0060 was miscalled part of the FEED stack — it is the clone-state
SQLite rewrite that shipped in the same campaign. The new "never widen the
scope you were given" bullet was kept, with ADR-0071 now stating plainly
that it restates the review-workflow scope rule rather than adding policy.
Verified and left alone: no bump/CHANGELOG (precedent — ADR-0056 itself
merged as `92d64f3` without touching either), supersession bookkeeping
consistent across four places, live ADR-0026 links all point at rules 2–4,
no stale label left on a live surface, and the MAP-inventory fixture change
is load-bearing (leave it at 0070 and the negative test's `replace` becomes
a no-op and the test fails).

**Next:** merge.
`.cursor/rules/tgcli-agent-contract.mdc` still points at `docs/DEVLOG.md`
for "newest entries" — stale since ADR-0058 moved devlog to per-session
files under `docs/devlog/`; left untouched here as out-of-scope for this
slice.
