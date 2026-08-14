# ADR-0119: Self-hosted macOS CI runner

Date: 2026-08-14
Status: accepted

Supersedes: ADR-0059 (decision 2 — the GitHub-hosted macOS CI leg).

## Context

The repository is private, so GitHub-hosted Actions minutes are metered.
The account hit its limit and GitHub refused to start any job
("recent account payments have failed or your spending limit needs to be
increased"), which froze all PRs regardless of their content. Paying for
more minutes was declined; the fix must run CI without GitHub-hosted
billing.

## Decision

1. A self-hosted macOS (Apple Silicon) runner, registered to this repo with
   labels `self-hosted,tgcli`, runs the whole CI. It is launched by a
   `LaunchAgent` (`com.github.actions-runner.tgcli`) so it survives login
   and needs no sudo.
2. CI is a single job, `pull_request` only: the full gate (`test`, including
   lint, pyright, and the doc gates, which are platform-independent but now
   run on macOS) on `[self-hosted, tgcli]`. The `push` trigger and the
   `test-macos` leg are removed — on a single runner they made every PR run
   three serialized full gates, and `test-macos` was a redundant second pass
   on the same platform.
3. `tests/test_repository_config.py` asserts the PR-only, single-job,
   `[self-hosted, tgcli]` shape, so the CI topology is pinned by the same
   repo-config guard that pinned the macOS leg before it.

## Rejected alternatives

- Making the repository public: cheapest fix, but visibility is an owner
  decision unrelated to CI cost.
- Paying for extra GitHub-hosted minutes: declined by the owner.
- Trimming CI (dropping the macOS leg or reducing the gate): macOS is a
  first-class target (ADR-0059, bf-03); shrinking the gate weakens the
  safety net to save minutes.

## Contract impact

- None on the CLI (no flags, JSON shapes, or exit codes change).
- CI now depends on the owner's Mac being online; a powered-off runner
  queues jobs until it returns. GitHub-hosted runners are no longer used.
