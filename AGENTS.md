# Agent Contract — tgcli

Canonical behavior contract for every AI agent working in this repo.

## Read First

1. [docs/MAP.md](docs/MAP.md) — where everything lives and what each module owns.
2. [docs/devlog/](docs/devlog/) — latest session entries: what happened
   recently and why. [docs/DEVLOG.md](docs/DEVLOG.md) is the closed history
   and template.
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

## Owner-Gated Development (ADR-0071, replacing ADR-0026's posture)

The project is in production use and still evolving; what gates it is the
owner, not a freeze. Default posture:

- **A new feature or behavior change needs an explicit owner request plus
  an ADR** — never a new phase in PLAN.md. A separate scoped plan is
  required only for a campaign: three or more PRs, or a new subsystem
  (ADR-0073); below that the ADR's decision section is the plan. An unvetted
  idea waits in docs/PROPOSALS.md; it does not become code.
- **A bug fix starts from a reproducing test**, then the minimal fix.
- **Never widen the scope you were given.** Adjacent improvements you spot
  are reported, not implemented. When in doubt whether something is a fix
  or a feature, ask the owner.

## Change Lanes (ADR-0073)

Ceremony follows risk, not size of ambition. A change takes the **full lane**
when it touches any of:

1. `docs/CONTRACT.md` semantics — CLI flags, JSON shapes, exit codes;
2. safety behavior — preview→commit, readonly gates, audit records, or any
   mutation path (send, edit, delete, forward, mark-read, clone writes);
3. session, config, or persistent state files, including their schemas;
4. request pacing and FloodWait handling (ADR-0072);
5. a new dependency, a new module, or a new abstraction;
6. what a **released** command does, as reachable from a release tag;
7. the enforcement mechanisms themselves — the logic of `scripts/gate.sh`,
   `check-architecture.py`, `check-docs.py`, `check-coverage.py`, or the CI
   workflows (the ceiling *numbers* stay integrator-owned under ADR-0058 and
   are not a trigger by themselves).

Anything else takes the **small-fix lane**: no ADR, no scoped plan, no ADR
index row, no `docs/PROPOSALS.md` / `docs/ISSUES.md` status edit, no release
bookkeeping. Ambiguous change — full lane.

The small lane keeps, without exception: the reproducing test first, the full
gate, the independent whole-diff review, the mirror-fix rule, and atomic
state writes. Those are what protect a live account; they are not ceremony.

**Documents ride with their code.** An ADR, plan, or devlog entry lands in the
PR that carries its implementation. A document-only PR is for a decision
deliberately taken before the work is scoped — an ADR proposed for owner
review, or a campaign plan spanning several PRs — never the default shape.

**Compatibility begins at a release tag.** Unreleased implementations are
replaceable and are not a sunk cost: reworking code that has not shipped
needs no superseding ADR.

## Documentation Discipline (mandatory)

- **Every landed slice** adds one devlog entry as its own file under
  `docs/devlog/` named `YYYY-MM-DD-slug.md` (template in `docs/DEVLOG.md`;
  ADR-0058, cadence amended by ADR-0073). Also write one for a session that
  produced a decision, an incident, or a handoff worth carrying; a session
  that landed nothing adds none. Target 15 lines — facts, not narrative.
  `docs/DEVLOG.md` and `docs/DEVLOG-v1.md` are closed: never append to them.
  Live-acceptance notes may name test-account aliases, but keep incident
  detail about real accounts impersonal (what broke and the fix — not which
  live account it happened to); never phone numbers or session material.
- **Every full-lane decision** (the seven triggers above) gets an ADR in
  `docs/decisions/` using the next number: `ADR-NNNN-slug.md`, plus its row in the index
  [docs/decisions/README.md](docs/decisions/README.md) in the same commit.
  XS/S changes may use the one-page ADR-lite form (ADR-0058): Context in
  one paragraph, Decision, Rejected alternatives, Contract impact. The
  full form stays mandatory for `docs/CONTRACT.md` semantics, safety
  behavior, and new dependencies.
  Superseding an old decision:
  new ADR + mark the old one `Status: superseded by ADR-NNNN`.
- **`docs/MAP.md` must match reality.** Added/moved/removed a module — update
  the map in the same commit.
- **Active summaries must close with the code.** A public command, global
  flag, safety guarantee, or guide page updates `README.md` / `SKILL.md` in
  the same slice. When a proposal or deferred issue graduates or ships,
  update its status in `docs/PROPOSALS.md` / `docs/ISSUES.md`; release
  bookkeeping alone is not closure. `scripts/check-docs.py` enforces the
  mechanically derivable parts (guide discoverability, root global flags,
  MAP inventory, benchmark claims, and devlog routing).
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
- Runtime boundary: never open a tgcli `.session` file with bare `python3` or
  a system/user-site Telethon. Use the `tg` entrypoint or `.venv/bin/python`
  from this checkout; `tg doctor` reports the active runtime for diagnosis.
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
  as part of the merge, never "later": `gh pr merge N --squash
  --delete-branch` (the ruleset enforces linear history, so `--merge` is
  rejected), then clean the local side with `git branch -d <topic>`
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
