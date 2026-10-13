# ADR-0121: GitHub-hosted CI for the public repository

Date: 2026-10-13
Status: accepted
Supersedes: ADR-0119

## Context

ADR-0119 moved CI to a self-hosted runner on the owner's Mac because the
repository was private and GitHub-hosted minutes were metered. The repository
is now public. That changes both sides of the trade:

- GitHub-hosted runners are free for public repositories, so the cost reason
  is gone.
- A `pull_request` job runs the PR's own code: tests, `conftest.py`, and the
  dependencies its lockfile names. On a public repository that code can come
  from any fork. The self-hosted runner shares a machine with the owner's
  Telegram session files, and a session file is full access to the account.
  GitHub's own guidance is not to use self-hosted runners on public
  repositories.

CI also ran only on `pull_request`, and the `main` ruleset does not require a
branch to be up to date before merging. The state of `main` after a squash
merge was therefore never checked, even on days when many PRs touching the
same modules landed back to back.

## Decision

1. CI runs on GitHub-hosted `macos-latest`. macOS stays the CI platform because
   it is the target platform (ADR-0059).
2. CI runs on `pull_request` and on `push` to `main`. PR runs cancel superseded
   runs of the same branch; `main` runs are never cancelled.
3. The self-hosted runner is no longer referenced by any workflow. Removing its
   registration and LaunchAgent from the Mac is the owner's step.

## Consequences

- CI no longer depends on the owner's Mac being online.
- A red `main` run means two PRs that were each green conflict in meaning;
  fix forward before fast-forwarding `tgcli-prod`.
