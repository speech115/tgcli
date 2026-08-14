## 2026-08-14 — Self-hosted CI runner (Composer)

**Did:** GitHub-hosted Actions stopped starting jobs (private repo, billing
limit hit). Set up a self-hosted macOS runner in `~/actions-runner-tgcli`
(labels `self-hosted,tgcli`), launched by a user `LaunchAgent`
(`com.github.actions-runner.tgcli`, no sudo). Routed both CI jobs to
`[self-hosted, tgcli]` in `.github/workflows/ci.yml`; updated
`tests/test_repository_config.py` to pin the new routing.

**Decided:** ADR-0119 — CI runs on the owner's Mac; GitHub-hosted minutes
dropped. ADR-0059 decision 2 (GitHub-hosted macOS leg) superseded.

**Learned:** `pull_request` jobs use the workflow from the base branch, not
the PR head — the `runs-on` change had to land in `main` before any PR's
checks would route to self-hosted.

**Next:** Re-check and merge PR #282 (coding standards), now green.
