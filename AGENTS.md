# tgcli — agent rules

tgcli is a stateless Telegram CLI over Telethon for its owner and agents:
JSON on stdout, fixed exit codes, and preview → commit for every write. No
daemons; state lives only in `~/.config/tgcli/` and `~/.local/state/tgcli/`.

- Using the CLI: [SKILL.md](SKILL.md) and `tg COMMAND --help`.
- What callers rely on (streams, exit codes, journal): [docs/CONTRACT.md](docs/CONTRACT.md).
  JSON shapes are pinned by `tests/snapshots/`.
- Running it, shipping `main`, troubleshooting: [README](README.md#operations).
- Backlog: GitHub Issues. History: `git log` and PR descriptions.

```bash
uv sync
git config core.hooksPath .githooks   # once per clone: static checks on push
./scripts/gate.sh                     # exactly what CI runs
```

## Rules

1. **The live account is real.** Agent sessions run with `TGCLI_READONLY=1`.
   Run a Telegram write (send, edit, delete, clone writes, `tg run --write`,
   login) only when the owner asked for it in this session, prefixed with
   `TGCLI_READONLY=0`. Never open a `.session` file with bare `python3`.
2. **stdout is contract data only**; diagnostics go to stderr via
   `output.note()`. A JSON change regenerates the snapshot
   (`UPDATE_SNAPSHOTS=1 uv run pytest tests/test_json_snapshots.py`); a
   removed or renamed field or flag means a `Breaking:` PR title. Keep the
   fields launchd jobs and gbrain read.
3. **Test at the CLI seam**: argv in; stdout, exit code and the exact
   Telegram requests out. Mock only the Telegram client (`FakeClient`), time
   and the filesystem. A bug fix starts with a test that fails on `main`.
4. **Ask the owner first** for new behavior, a new dependency or a new
   subsystem.

`scripts/check-bans.py` (in the gate) keeps three ban risks out: catch floods
through `tg_errors.FLOOD_ERRORS`, pass `wait_time` to every `iter_messages`,
write state through `tgcli.atomic.replace_text`. To prove a change against
real Telegram, read-only, use [.claude/skills/verify/SKILL.md](.claude/skills/verify/SKILL.md).
`scripts/repro.py` and `scripts/mutate.py` are optional helpers.

## Git

Branch `claude/<topic>`, one change per PR to `main`, never push to `main`.
Enable squash auto-merge on the PR; CI must pass. Release only when asked.

## Language

Talk to the owner in Russian. Code, comments, docs, commits and CLI output are
English.
