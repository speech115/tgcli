# Claude Code Notes — tgcli

Canonical contract: [AGENTS.md](AGENTS.md). Read it first, then
[docs/MAP.md](docs/MAP.md) and the tail of [docs/DEVLOG.md](docs/DEVLOG.md).

## Agent skills

### Issue tracker

Work items live in GitHub Issues. See
[docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).

### Triage labels

The repository uses the five canonical engineering-flow labels. See
[docs/agents/triage-labels.md](docs/agents/triage-labels.md).

### Domain docs

This is a single-context repository; `docs/decisions/` remains the only ADR
directory. See [docs/agents/domain.md](docs/agents/domain.md).

## Language
Respond in Russian. Code, commits, docs, CLI output stay in English.

## Quick Orientation
- Current scope and re-entry gates: [docs/ISSUES.md](docs/ISSUES.md)
- Historical master plan: [docs/PLAN.md](docs/PLAN.md)
- CLI output/exit-code contract: [docs/CONTRACT.md](docs/CONTRACT.md)
- Decisions (ADR): [docs/decisions/](docs/decisions/)
- Test command: `uv run pytest -q` from repo root

## Hard Rules
- No daemons or background processes — this project exists because the
  previous stack (tools/telegram) was daemon-first and fragile.
- Session files and secrets never enter the repo.
- Add a devlog entry file under docs/devlog/ at the end of every session
  (see AGENTS.md; ADR-0058).
