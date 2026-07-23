# Agent Contract — tgcli

Canonical behavior contract for every AI agent working in this repo.

## Read First

1. [docs/MAP.md](docs/MAP.md) — where everything lives and what each module owns.
2. [docs/DEVLOG.md](docs/DEVLOG.md) — last entries: what happened recently and why.
3. [docs/ISSUES.md](docs/ISSUES.md) — current scope: deferred work and re-entry gates.
4. Relevant ADRs before touching an area they govern — start from the
   index in [docs/decisions/README.md](docs/decisions/README.md).

[docs/PLAN.md](docs/PLAN.md) (completed master plan) and
[docs/CLONE.md](docs/CLONE.md) (clone chronicle) are historical
background, not current scope.

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
- **Every architectural decision** (new dependency, new module, changed
  contract, changed safety behavior) gets an ADR in `docs/decisions/`
  using the next number: `ADR-NNNN-slug.md`, plus its row in the index
  [docs/decisions/README.md](docs/decisions/README.md) in the same commit.
  Superseding an old decision:
  new ADR + mark the old one `Status: superseded by ADR-NNNN`.
- **`docs/MAP.md` must match reality.** Added/moved/removed a module — update
  the map in the same commit.
- **`docs/CONTRACT.md` is versioned law.** Any change to CLI flags, JSON
  shapes, or exit codes updates CONTRACT.md in the same commit. Breaking
  changes require an ADR.

## Engineering Rules

- TDD: failing test → minimal code → green → commit. No production code
  without a test that demanded it.
- YAGNI aggressively. This project replaces a 200k-LOC stack; the whole
  point is staying small. New abstraction needs an ADR.
- Stateless: no background processes, no state outside
  `~/.config/tgcli/` (config) and `~/.local/state/tgcli/` (sessions,
  locks, audit, cache).
- stdout is sacred: only contract data. Debug/progress/warnings → stderr.
- Never commit: `.env`, `*.session`, audit logs, downloaded media,
  anything under `~/.local/state/tgcli/`.
- Run `pytest -q`, `ruff check .`, `ruff format --check .`, and `pyright`
  before every commit (CI enforces all four — ADR-0027). Quote real output
  in PRs, never "tests pass".

## Git

- Branch: `claude/<topic>` or `codex/<topic>`.
- Commit: single-line imperative summary (`Add dialogs command`).
- Never push to `main` without an explicit current-session request.

## Language

User-facing conversation: Russian. Code, comments, docs in this repo,
commits, CLI output: English.

## Cursor Cloud specific instructions

- Tooling is `uv`-managed. Standard commands are in `.github/workflows/ci.yml`
  and the "Engineering Rules" above: `uv run pytest -q`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run pyright`, and the coverage gate
  `uv run python scripts/check-coverage.py`. Run the CLI with `uv run tg ...`.
- `uv` installs to `~/.local/bin` and is added to PATH via `~/.bashrc`
  (`. "$HOME/.local/bin/env"`); non-login/non-interactive shells may need
  `PATH="$HOME/.local/bin:$PATH"` prefixed. The startup update script runs
  `uv sync`.
- Live Telegram commands (`dialogs`, `read`, `send`, `clone sync`, etc.) need
  real credentials: an account in `~/.config/tgcli/config.toml` with a valid
  `api_id`/`api_hash` (from https://my.telegram.org) plus an authenticated
  `*.session` file under `~/.local/state/tgcli/sessions/`. These are user
  secrets and are never in the repo, so those commands cannot run in a fresh
  cloud VM without them.
- Offline commands need no network/credentials and are the way to verify the
  build end-to-end: `tg accounts list`, `tg doctor` (filesystem/session health),
  `tg clone status`, and the pre-network write gates (`--readonly` /
  `TGCLI_NO_SEND=1` return exit code 2 / `BLOCKED`). `tg doctor` requires a
  config file to exist; without one it exits 3 (`CONFIG`).
