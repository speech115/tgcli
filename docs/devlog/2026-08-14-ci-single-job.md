## 2026-08-14 — CI collapsed to one PR job (Composer)

**Did:** The first self-hosted CI ran three serialized full gates per PR
(`branch-test` on push + `test` + `test-macos` on PR), ~10 min a merge.
Collapsed `.github/workflows/ci.yml` to a single `test` job, `pull_request`
only; dropped the `push` trigger and the redundant `test-macos` leg. Updated
`tests/test_repository_config.py` and ADR-0119 to pin the new shape.

**Learned:** A single self-hosted runner turns every extra job/trigger into
serial wall-clock, so job count is the dominant cost, not step count.

**Next:** Merge when green; every PR now waits one ~2–4 min gate.
