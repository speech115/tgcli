# tgcli — agent contract

tgcli is a stateless Telegram CLI over Telethon, for humans and agents: JSON
on stdout, fixed exit codes, and preview → commit for every write. It replaced
a daemon-first stack, so staying small and daemonless is the point.

- Using the CLI: [SKILL.md](SKILL.md) and `tg COMMAND --help`.
- CLI law (flags, JSON shapes, exit codes): [docs/CONTRACT.md](docs/CONTRACT.md).
- Vocabulary: [CONTEXT.md](CONTEXT.md).
- Why past decisions were made: [docs/decisions/](docs/decisions/README.md).
  ADRs are dated history, not kept current; CONTRACT and the gate say what
  holds now.
- Backlog: GitHub Issues (`gh issue list`).

## Commands

```bash
uv sync
./scripts/gate.sh   # exactly what CI runs, about 15 s
uv run tg --help
```

A green gate proves the code matches the fakes. To prove a behavior change
against real Telegram, read-only, follow
[.claude/skills/verify/SKILL.md](.claude/skills/verify/SKILL.md).

## Rules a check enforces

Breaking one of these fails the gate. Don't restate them elsewhere. If a check
is wrong, fix the check.

| Rule | Enforced by |
|---|---|
| stdout carries contract data only; diagnostics go to stderr through `output.note()` | ruff `T20` (no `print`), `tests/test_output.py` |
| State that is read back later is written with `tgcli.atomic.replace_text`, never `write_text` | `scripts/check-architecture.py` |
| Read commands are reachable only through `read_ops` | `scripts/check-architecture.py` |
| No module passes 800 lines; split it instead (two listed exceptions) | `scripts/check-architecture.py` (`MAX_LINES`) |
| Exit codes come from the `errors.py` hierarchy and match CONTRACT §4 | `tests/test_contract_exit_codes.py` |
| Every mutation goes through preview → commit and the readonly gates | `src/tgcli/preview_commit.py` registry, `tests/test_preview_commit.py`, `tests/test_safety.py` |
| Audit is written before the mutation, and an unwritable audit blocks it | `tests/test_safety.py`, `tests/test_cli_mutate.py` |
| Every Telethon namespace is classified; `tg run` refuses writes without `--write` and auth/account always | `scripts/check-coverage.py` with `docs/FEATURES.md`, `tests/test_cli_run.py` |
| README and SKILL.md name only real flags, commands, and links | `scripts/check-docs.py` |
| Sessions, `.env`, audit and journal files never enter git | `.gitignore` |
| Merged branches are deleted and history stays linear | GitHub repository settings |

## Rules that need judgment

Nothing fails if you skip these, so they stay few.

- **The live account is real.** Run a Telegram mutation (send, edit, delete,
  forward, clone writes, `tg run --write`, login) only when the owner asked for it in
  this session. Read-only checks are fine.
- **Never open a `.session` file with bare `python3`** or a system Telethon.
  Use `uv run tg` or `.venv/bin/python`: the pinned Telethon writes a schema
  older versions crash on.
- **A bug fix starts with a reproducing test**, then the smallest fix. Grep for
  sibling code with the same pattern and fix it in the same PR.
- **Test behavior at public seams**: CLI arguments in; stdout, stderr, JSON,
  and exit code out. Mock only the Telegram client, time, and the filesystem.
  A test for a new or changed RPC asserts the exact `functions.*Request` and
  `types.Input*` it sends.
- **A CLI contract change** (flags, JSON, exit codes) updates
  `docs/CONTRACT.md` and `SKILL.md` in the same PR. Renaming or removing a
  command, flag, or field is allowed; start the PR title with `Breaking:`.
- **Ask the owner first** for new behavior, a new dependency, or a new
  subsystem. Never add a daemon or state outside `~/.config/tgcli/` and
  `~/.local/state/tgcli/`. Write an ADR only for a decision that is hard to
  reverse.
- **Don't widen scope.** Report adjacent problems instead of fixing them in the
  same PR.

## When the owner corrects you

Fix the mistake, then make it impossible to repeat. Prefer architecture, then a
lint or test in the gate, and add a line to the judgment list only as a last
resort. A rule that gains a check moves to the table above. A rule whose
mistake can no longer happen is deleted.

## Git

- Branch `claude/<topic>` or `codex/<topic>`, one coherent change per PR to
  `main`. Never push to `main` unless asked in this session.
- Run the gate before pushing; CI runs the same gate.
- Merge with `gh pr merge N --squash` only after CI is green.
- There are no releases, tags, or changelog: `main` is what runs, and git log
  is the history.

## Language

Talk to the owner in Russian. Code, comments, docs, commits, and CLI output are
English.
