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

## Wayfinding operations

`/wayfinder` uses one map issue plus child decision issues.

- **Map:** create one issue labelled `wayfinder:map`. Its body owns
  Notes, Decisions-so-far, and Fog.
- **Child:** link each decision ticket as a native GitHub sub-issue:
  `gh api repos/<owner>/<repo>/issues/<map>/sub_issues -F sub_issue_id=<child-database-id>`.
  Use a task list in the map plus `Part of #<map>` in the child only when
  sub-issues are unavailable. Label the child `wayfinder:<type>`, where type
  is `research`, `prototype`, `grilling`, or `task`.
- **Blocking edge:** use GitHub's native issue-dependency endpoint:
  `gh api repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-database-id>`.
  If dependencies are unavailable, put `Blocked by: #<number>, ...` at the
  top of the child.
- **Ids for both endpoints:** `sub_issue_id` and `issue_id` are numeric
  database ids, not the `#number` and not the GraphQL `node_id`. Fetch one
  with `gh api repos/<owner>/<repo>/issues/<number> --jq .id`. Pass it with
  `gh api -F` (typed), never `-f`: `-f` sends the value as a JSON string and
  both endpoints reject it with HTTP 422
  `Invalid property /<field>: "<id>" is not of type integer`.
- **Frontier:** list the map's open children in map order. Exclude assigned
  children and any child whose `issue_dependencies_summary.blocked_by` is
  non-zero (or whose fallback `Blocked by` issue remains open). The first
  remaining child is the next claimable ticket.
- **Claim:** `gh issue edit <number> --add-assignee @me`. Claiming is the
  session's first tracker write.
- **Resolve:** comment with the answer, close the child, then append a compact
  pointer to its result under the map's Decisions-so-far section.
