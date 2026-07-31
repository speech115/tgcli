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

**Next:** independent Spec + Standards review of the branch, then merge.
`.cursor/rules/tgcli-agent-contract.mdc` still points at `docs/DEVLOG.md`
for "newest entries" — stale since ADR-0058 moved devlog to per-session
files under `docs/devlog/`; left untouched here as out-of-scope for this
slice.
