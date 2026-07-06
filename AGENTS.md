# Agent Contract — tgcli

Canonical behavior contract for every AI agent working in this repo.

## Read First

1. [docs/MAP.md](docs/MAP.md) — where everything lives and what each module owns.
2. [docs/PLAN.md](docs/PLAN.md) — current phase, what is in/out of scope.
3. [docs/DEVLOG.md](docs/DEVLOG.md) — last entries: what happened recently and why.
4. Relevant ADRs in [docs/decisions/](docs/decisions/) before touching an area
   they govern.

## Documentation Discipline (mandatory)

- **Every working session** appends one entry to `docs/DEVLOG.md`
  (template inside the file). No entry — the session did not happen.
- **Every architectural decision** (new dependency, new module, changed
  contract, changed safety behavior) gets an ADR in `docs/decisions/`
  using the next number: `ADR-NNNN-slug.md`. Superseding an old decision:
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
- Run `pytest -q` before every commit. Quote real output in PRs, never
  "tests pass".

## Git

- Branch: `claude/<topic>` or `codex/<topic>`.
- Commit: single-line imperative summary (`Add dialogs command`).
- Never push to `main` without an explicit current-session request.

## Language

User-facing conversation: Russian. Code, comments, docs in this repo,
commits, CLI output: English.
