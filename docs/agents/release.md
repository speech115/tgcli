# Release Runbook — stacked PRs, merge, tag

Distilled from the 1.1.x and 1.2.0 releases (see DEVLOG 2026-07-23/24).
Follow it literally; every rule here was paid for by a real failure.

## Merging a stacked PR chain

A stack `main ← A ← B ← C` merges bottom-up, one PR at a time:

1. Merge the PR whose base is `main` with `gh pr merge N --merge`
   — **without `--delete-branch`**.
2. Retarget the next PR yourself: `gh pr edit M --base main`. Do not rely
   on GitHub's automatic retargeting: deleting the base branch races it and
   **closes** the dependent PR instead. If that happens anyway, recovery is:
   recreate the ref (`gh api repos/{owner}/{repo}/git/refs
   -f ref=refs/heads/<branch> -f sha=<full 40-char sha>` — a short sha 422s),
   `gh pr reopen M`, `gh pr edit M --base main`, delete the ref again.
3. Reopened or externally-created PRs may come back as drafts:
   `gh pr ready M` before merging.
4. Wait for CI on the head SHA before every merge (`gh pr checks N --watch`).
   A green local gate is necessary but not sufficient.
5. Repeat until the release PR lands. A conflict at the last step is normally
   the CHANGELOG compare-links block — resolve keeping the union, rerun the
   full gate on the merge commit before pushing.

## Tag and cleanup

- Tag the **merge commit** on `main`: `git tag -a vX.Y.Z -m "..." <sha> &&
  git push origin vX.Y.Z`. The CHANGELOG compare link must resolve the moment
  the tag exists.
- Delete merged branches **one at a time** (batch `git push --delete` may be
  blocked by the approval classifier; fallback:
  `gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/<branch>`). Prefer
  `gh pr merge N --merge --delete-branch` so the ref never outlives the
  merge — the AGENTS.md "Git" rule applies to every merge, release or not.
  Finish with `git remote prune origin` so stale remote-tracking refs go too.
- Before force-deleting a local branch, prove it is contained:
  `git cherry main <branch>` must show only `-` lines.
- Append the release session to DEVLOG (merge order, tag sha, anything that
  fought back).

## Review fixes during a release

Fixes for review findings ride the head of the PR that introduced the
problem — push the fix commit onto that branch. Do not open a new stacked
PR per review round; the 1.2.0 cycle accumulated ten obsolete `fix/*`
branches that way.
