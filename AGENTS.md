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

- **Every working session** appends one entry to `docs/DEVLOG.md`
  (template inside the file). No entry — the session did not happen.
  `docs/DEVLOG-v1.md` is closed: never append to it.
- **Every architectural decision** (new dependency, new module, changed
  contract, changed safety behavior) gets an ADR in `docs/decisions/`
  using the next number: `ADR-NNNN-slug.md`, plus its row in the index
  [docs/decisions/README.md](docs/decisions/README.md) in the same commit.
  Superseding an old decision:
  new ADR + mark the old one `Status: superseded by ADR-NNNN`.
- **`docs/MAP.md` must match reality.** Added/moved/removed a module — update
  the map in the same commit.
- **A feature or fix that changes `docs/CONTRACT.md` ships as a release** (ADR-0038):
  bump the **patch** version in `pyproject.toml` and `src/tgcli/__init__.py`,
  add the `CHANGELOG.md` section naming its ADR, all in the same commit —
  then tag the merged release commit `vX.Y.Z`. Never let unreleased contract
  changes accumulate. The
  minor digit is raised only when the owner declares a milestone.
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
- Never commit: `.env`, `*.session`, audit logs, downloaded media,
  anything under `~/.local/state/tgcli/`.
- Run `uv run pytest -q`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run pyright`, and
  `uv run python scripts/check-coverage.py` before every commit. Quote real
  output in PRs, never "tests pass".

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

## Language

User-facing conversation: Russian. Code, comments, docs in this repo,
commits, CLI output: English.
