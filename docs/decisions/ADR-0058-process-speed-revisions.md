# ADR-0058: Process-speed revisions — integrator releases, per-entry devlog, ceiling grace

Date: 2026-07-26
Status: accepted; decision 3 (grace band) superseded by ADR-0120; decisions 1–2 (integrator releases, per-session devlog) superseded by ADR-0120

## Context

Owner question after the 1.2.16 hardening campaign: changes feel slow — is
the process worth revisiting? Measured against the merged PR history
(39 merged PRs) rather than intuition; the full dataset lives in
`docs/PROPOSALS.md` ("Process-speed rule revisions"). The short version:

- Neither test runtime nor merge latency is the cost. The full gate runs in
  52 s; PR cycle time is median 1.2 h open→merge, none over a day.
- The measured cost is volume plus serialization: shared files conflict by
  construction. Of 133 commits in the campaign-era history, `docs/DEVLOG.md`
  appears in 54, `scripts/check-architecture.py` ceilings in 38,
  `CHANGELOG.md` in 27.
- The release-per-change mechanics of ADR-0038 misfired three times in
  practice: a version race between parallel branches (1.2.10 vs 1.2.11),
  a `__version__` drift fix (PR #51), a CHANGELOG finalized after tagging
  (PR #38) — and tags `v1.2.10`–`v1.2.15` were never created because agent
  sessions cannot push tags.

Explicitly **not** revised, with the evidence for keeping them: red-first
tests, boundary tests, the independent pre-merge review, and the full gate
before commit. Wave 1 of the hardening campaign came back needs-work on 4
of 5 slices and PR #77's review found two confirmed majors — these rules
are the defect catchers, and the gate costs 52 s.

## Decision

1. **The integrator assigns releases.** A feature branch that changes
   `docs/CONTRACT.md` never touches the version files, `CHANGELOG.md`, or a
   tag. The session that merges the change (the integrator) bumps the patch
   version in `pyproject.toml` + `src/tgcli/__init__.py`, writes the
   CHANGELOG section naming the ADR, and lands both in the merge that lands
   the change. ADR-0038's intent — no unreleased contract changes
   accumulate — is unchanged; only *who and when* moves. This amends
   ADR-0038's rule 3 mechanics.
2. **Devlog entries are one file per session** under `docs/devlog/`, named
   `YYYY-MM-DD-slug.md`, using the same template. `docs/DEVLOG.md` is
   closed for appends (like `DEVLOG-v1.md`): read it for history, never
   write it. Parallel sessions can no longer conflict on a shared
   newest-on-top file.
3. **Line ceilings get a grace band.** `scripts/check-architecture.py`
   passes a file that exceeds its reviewed ceiling by up to `GRACE = 50`
   lines, warning on stderr; growth past ceiling + 50 still fails
   everywhere. Feature branches therefore never edit ceilings or their test
   mirror. The integrator trues ceilings up (or down) at merge, verified
   with `--strict` (zero grace).
4. **Parallel waves branch from the integration head**, never from `main`,
   when a campaign or multi-slice effort has its own integration branch.
   All six wave-2 worktrees of the 1.2.16 campaign branched from `main`:
   every cherry-pick needed ceiling reconciliation and one slice re-invented
   `DeadlineExceeded` that wave 1 had already landed.
5. **ADR-lite for XS/S changes.** A one-page ADR — Context (one paragraph),
   Decision, Rejected alternatives, Contract impact — is enough for small
   scoped changes. The full form stays mandatory for anything touching
   `docs/CONTRACT.md` semantics, safety behavior, or a new dependency.

## Rejected

- Weakening or skipping the independent review, red-first tests, or the
  gate: the measured hit rate says they pay for themselves.
- Removing ceilings entirely: the grace band keeps the anti-bloat stop.
- A release train that batches several merged changes into one version:
  rejected as reintroducing exactly the drift ADR-0038 exists to prevent;
  integrator-at-merge keeps one-change-one-release.

## Consequences

- The shared-file set shrinks to `docs/CONTRACT.md` plus the version/
  CHANGELOG pair, and the latter is only ever written by one session (the
  integrator) — the conflict class is gone by construction.
- A merged PR's diff no longer contains its own version bump; the release
  commit is the merge (or an immediately following integrator commit).
  `CHANGELOG.md` remains the consumer-facing record.
- The tag question (v1.2.10+ gap) stays owner-side: tags still cannot be
  pushed from agent sessions. The integrator lists owed tags in the merge
  report until the owner tags or amends ADR-0038's tag rule.
- `docs/DEVLOG.md` joins the closed-history set; discovery is `ls
  docs/devlog/` (dates sort lexicographically).
