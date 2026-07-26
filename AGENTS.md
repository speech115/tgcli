# Agent Contract — tgcli

Canonical behavior contract for every AI agent working in this repo.

## Read First

1. [docs/MAP.md](docs/MAP.md) — where everything lives and what each module owns.
2. [docs/DEVLOG.md](docs/DEVLOG.md) — last entries: what happened recently and why.
3. [docs/ISSUES.md](docs/ISSUES.md) — current scope: deferred work and re-entry gates.
4. Relevant ADRs before touching an area they govern — start from the
   index in [docs/decisions/README.md](docs/decisions/README.md).
5. When using installed engineering flows, read the matching repository
   routing under [docs/agents/](docs/agents/): issue tracker, triage labels,
   and domain-document discovery.

[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) are the
outward-facing summaries of these rules (ADR-0056): the first restates this
contract for human contributors, the second owns the private reporting channel
and the redaction rules. **This file stays canonical** — where either drifts
from it, the other document is the bug.

[docs/PLAN.md](docs/PLAN.md) (completed master plan),
[docs/CLONE.md](docs/CLONE.md) (clone chronicle),
[docs/DEVLOG-v1.md](docs/DEVLOG-v1.md) (sessions up to 1.0.0), and everything
under [docs/superpowers/](docs/superpowers/) (completed plans and specs) are
**closed history**. Read them to chase how something came to be; never to
learn how it behaves now, and never update them when behaviour changes.

## Agent Skills

- Work items live in GitHub Issues; pull requests are not an incoming triage
  surface. See [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).
- Triage flows use the canonical label mapping in
  [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
- This is a single-context repository. `docs/decisions/` is the only ADR
  directory; do not create `docs/adr/`. See
  [docs/agents/domain.md](docs/agents/domain.md).

## Maintenance Mode (ADR-0026, since 2026-07-17)

The project is feature-complete and in production use. Default posture:

- **Do not add features.** A new feature or behavior change needs an
  explicit owner request plus an ADR and a scoped plan — never a new
  phase in PLAN.md.
- **A bug fix starts from a reproducing test**, then the minimal fix.
- When in doubt whether something is a fix or a feature, ask the owner.

## Documentation Discipline (mandatory)

- **Every working session** adds one devlog entry as its own file under
  `docs/devlog/` named `YYYY-MM-DD-slug.md` (template in `docs/DEVLOG.md`;
  ADR-0058). No entry — the session did not happen. `docs/DEVLOG.md` and
  `docs/DEVLOG-v1.md` are closed: never append to them.
  Live-acceptance notes may name test-account aliases, but keep incident
  detail about real accounts impersonal (what broke and the fix — not which
  live account it happened to); never phone numbers or session material.
- **Every architectural decision** (new dependency, new module, changed
  contract, changed safety behavior) gets an ADR in `docs/decisions/`
  using the next number: `ADR-NNNN-slug.md`, plus its row in the index
  [docs/decisions/README.md](docs/decisions/README.md) in the same commit.
  XS/S changes may use the one-page ADR-lite form (ADR-0058): Context in
  one paragraph, Decision, Rejected alternatives, Contract impact. The
  full form stays mandatory for `docs/CONTRACT.md` semantics, safety
  behavior, and new dependencies.
  Superseding an old decision:
  new ADR + mark the old one `Status: superseded by ADR-NNNN`.
- **`docs/MAP.md` must match reality.** Added/moved/removed a module — update
  the map in the same commit.
- **A feature or fix that changes `docs/CONTRACT.md` ships as a release**
  (ADR-0038, mechanics amended by ADR-0058): the **integrator** — the
  session that merges — bumps the **patch** version in `pyproject.toml` and
  `src/tgcli/__init__.py` and adds the `CHANGELOG.md` section naming the
  ADR **and its `[x.y.z]:` compare link**, in the merge that lands the
  change. Feature branches never touch the version files, `CHANGELOG.md`,
  or tags. The docs gate refuses a release section without its link; the
  `Release tag` workflow tags the merge commit `vX.Y.Z` on push to `main`
  whenever the push moved `__version__`. Never let unreleased contract
  changes accumulate. The minor digit is raised only when the owner
  declares a milestone.
- **`docs/CONTRACT.md` is versioned law.** Any change to CLI flags, JSON
  shapes, or exit codes updates CONTRACT.md in the same commit. Breaking
  changes require an ADR.

## Engineering Rules

- TDD: failing test → minimal code → green → commit. No production code
  without a test that demanded it.
- Test behavior at public seams. CLI work is verified through arguments,
  stdout/stderr, JSON, and exit codes. A new or changed Telegram RPC also
  needs a boundary test that asserts the exact Telethon request and input
  types; permissive fakes are not proof that Telegram will accept a request.
- YAGNI aggressively. This project replaces a 200k-LOC stack; the whole
  point is staying small. New abstraction needs an ADR.
- Stateless: no background processes, no state outside
  `~/.config/tgcli/` (config) and `~/.local/state/tgcli/` (sessions,
  locks, audit, cache).
- stdout is sacred: only contract data. Debug/progress/warnings → stderr.
- State files that are read back later are replaced atomically via
  `tgcli.atomic.replace_text`, never `write_text`
  (`scripts/check-architecture.py` enforces this for state-writing modules).
- **Mirror-fix rule:** fixed a bug class in one subsystem — grep for the
  sibling subsystems that share the pattern and port the fix plus its
  regression test in the same commit. (The preview mtime-fallback existed
  while logins shipped without it; that gap became the 1.2.0 critical.)
- Never commit: `.env`, `*.session`, audit logs, downloaded media,
  anything under `~/.local/state/tgcli/`.
- Run `./scripts/gate.sh` (ruff check + format, architecture, pyright,
  pytest, coverage matrix, docs gate — the exact CI steps) before every
  commit. Quote real output in PRs, never "tests pass".

## Implementation and Review Workflow

- Keep a PR to one coherent slice, or two tightly coupled slices. Unrelated
  onboarding, tooling, cleanup, and product behavior belong in separate PRs.
- The implementation agent owns the red → green loop and the focused tests.
  Green focused tests or green CI are necessary, not sufficient evidence that
  the PR is ready.
- Before merge, perform an independent whole-diff review from the merge-base.
  Prefer a different agent or a fresh review context; the implementation
  agent must not be the only final reviewer of its own work.
- Review on two axes:
  1. **Spec:** every ADR/plan/CONTRACT requirement is implemented, and no
     unapproved behavior was added.
  2. **Standards:** AGENTS, module ownership, stdout, exit-code, safety,
     audit, documentation, and code-smell rules are respected.
- Adversarial review is mandatory for CLI boundaries: invalid and combined
  flags, empty input, caps, ISO date coercion, partial failures, readonly
  gates, audit timing, and exact external-library types where applicable.
- Every confirmed review defect starts with a permanent reproducing test,
  then the minimal fix. Rerun the full gate after all review fixes; do not
  present focused checks as final proof.
- If mocked tests cannot prove external behavior, add a safe live smoke.
  Telegram mutations remain owner-gated and must never be inferred from a
  review or verification request.

## Git

- Branch: `claude/<topic>` or `codex/<topic>`.
- Commit: single-line imperative summary (`Add dialogs command`).
- After a requested slice/task passes the full gate and docs are updated,
  **commit and push the feature branch** in the same turn — do not wait for
  a separate "commit" / "push" ask. Still never push to `main` without an
  explicit current-session request.
- Never push to `main` without an explicit current-session request.
- Never merge a PR while its head-SHA checks are pending or red — wait with
  `gh pr checks N --watch`. The repo has no enforced branch protection;
  this rule is the protection.
- Review-fix commits go onto the head of the PR under review, not onto a
  new branch. One branch per slice, not per review round.
- **Shared files belong to the integrator.** When several agents work in
  parallel (a worktree per slice), the files every slice touches are not
  theirs to edit: `scripts/check-architecture.py` line ceilings and their
  `tests/test_check_architecture.py` mirror (the ADR-0058 grace band means
  a slice rarely needs a ceiling touched at all), `docs/CONTRACT.md`,
  `CHANGELOG.md`, and the version in `pyproject.toml` /
  `src/tgcli/__init__.py` (integrator-only under ADR-0058). An agent
  needing a contract line reports it instead; the integrator lands all of
  them once. Devlog entries are per-session files under `docs/devlog/` and
  never conflict.
- **Parallel waves branch from the integration head**, never from `main`,
  whenever a campaign has its own integration branch (ADR-0058): a wave
  based on `main` cannot see the seams earlier waves already landed.
- **A merged branch does not survive the session that merged it.** Delete it
  as part of the merge, never "later": `gh pr merge N --merge
  --delete-branch`, then clean the local side with `git branch -d <topic>`
  and `git remote prune origin`. This applies to every merge, not only
  releases.
- Before ending a session that merged anything, `git branch -a` must show
  nothing but `main` and branches with a still-open PR. Verify with
  `git branch --merged main` — anything it lists besides `main` is garbage
  and goes. Use `git branch -d` (never `-D`) so git refuses when a branch is
  not actually contained.
- Releases and stacked-PR merges follow
  [docs/agents/release.md](docs/agents/release.md) literally.

## Language

User-facing conversation: Russian. Code, comments, docs in this repo,
commits, CLI output: English.
