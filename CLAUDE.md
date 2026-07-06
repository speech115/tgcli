# Claude Code Notes — tgcli

Canonical contract: [AGENTS.md](AGENTS.md). Read it first, then
[docs/MAP.md](docs/MAP.md) and the tail of [docs/DEVLOG.md](docs/DEVLOG.md).

## Language
Respond in Russian. Code, commits, docs, CLI output stay in English.

## Quick Orientation
- Master plan and current phase: [docs/PLAN.md](docs/PLAN.md)
- CLI output/exit-code contract: [docs/CONTRACT.md](docs/CONTRACT.md)
- Decisions (ADR): [docs/decisions/](docs/decisions/)
- Test command: `pytest -q` from repo root (uv-managed venv, see pyproject.toml)

## Hard Rules
- No daemons or background processes — this project exists because the
  previous stack (tools/telegram) was daemon-first and fragile.
- Session files and secrets never enter the repo.
- Update DEVLOG.md at the end of every session (see AGENTS.md).
