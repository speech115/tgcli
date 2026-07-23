# Issue tracker: GitHub

Issues and PRDs for this repository live as GitHub issues. Use the `gh` CLI
from this checkout so the repository is inferred from `origin`.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`.
- Read: `gh issue view <number> --comments`.
- List: `gh issue list --state open --json number,title,body,labels,comments`.
- Comment: `gh issue comment <number> --body "..."`.
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- Close: `gh issue close <number> --comment "..."`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

Pull requests are implementation and review artifacts, not incoming product
requests. A bare GitHub `#<number>` can refer to either an issue or a pull
request; resolve it with `gh pr view` and fall back to `gh issue view`.

## Skill routing

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", use
  `gh issue view <number> --comments`.
- `/to-tickets` creates GitHub issues and records blocking edges with native
  issue dependencies when available.
- `/wayfinder` uses one issue labelled `wayfinder:map` plus linked child
  issues. If native sub-issues or dependencies are unavailable, use a task
  list in the map and explicit `Blocked by: #<number>` lines.

