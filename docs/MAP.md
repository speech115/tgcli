# Project Map

Status legend: `[planned]` — not built yet, `[wip]`, `[done]`.
This file must always match the real tree (AGENTS.md rule).

```
tgcli/
├── README.md                  [done]    vision + principles
├── AGENTS.md                  [done]    agent contract, doc discipline
├── CLAUDE.md                  [done]    Claude adapter → AGENTS.md
├── pyproject.toml             [planned] uv-managed; deps: telethon; dev: pytest
├── docs/
│   ├── MAP.md                 [done]    this file
│   ├── PLAN.md                [done]    master plan, phases 0–6
│   ├── CONTRACT.md            [done]    CLI automation contract (stdout/exit codes/JSON)
│   ├── DEVLOG.md              [done]    session-by-session agent log
│   ├── FEATURES.md            [done]    TL-namespace coverage matrix (ADR-0008; trued up in phase 7)
│   ├── decisions/             [done]    ADR-0001…0007 (see index below)
│   └── superpowers/plans/     [done]    per-phase TDD implementation plans
├── src/tgcli/
│   ├── __init__.py            [planned] version string only
│   ├── cli.py                 [planned] argparse tree, global flags, dispatch, exit-code mapping
│   ├── output.py              [planned] emit(data) → stdout as JSON/plain; note()/warn() → stderr
│   ├── errors.py              [planned] TgcliError hierarchy ↔ exit codes (CONTRACT.md §4)
│   ├── config.py              [planned] ~/.config/tgcli/config.toml, accounts registry, alias resolution
│   ├── session.py             [planned] session paths, per-account file lock, TelegramClient factory
│   ├── safety.py              [planned] --readonly / TGCLI_NO_SEND / write-audit checks (phase 4)
│   ├── commands/
│   │   ├── accounts.py        [planned] tg accounts list|add|import   (phase 1 / import in 6)
│   │   ├── dialogs.py         [planned] tg dialogs                    (phase 1)
│   │   ├── read.py            [planned] tg read <chat>                (phase 1)
│   │   ├── search.py          [planned] tg search / count / latest / info / message (phase 2)
│   │   ├── media.py           [planned] tg media download             (phase 3)
│   │   ├── send.py            [planned] tg send --preview/--commit    (phase 4)
│   │   ├── api.py             [planned] tg api raw TL passthrough     (phase 2 read / 4 write, ADR-0008)
│   │   └── export.py          [planned] tg export messages|subscribers (phase 5, takeout)
│   └── backends/
│       └── tdlib.py           [planned] optional media fallback for private channels (phase 3)
├── tests/                     [planned] unit tests, mocked Telethon client
│   └── live/                  [planned] gated live smoke (TGCLI_LIVE_SMOKE=1)
└── scripts/
    ├── install-link.sh        [planned] symlink tg → PATH (phase 6 cutover)
    └── check-coverage.py      [planned] TL namespaces vs FEATURES.md (phase 7 gate)
```

## Module Ownership Rules

- `commands/*` never talk to Telethon directly for connection management —
  always through `session.client(account)` context manager.
- `commands/*` never print — they return data structures; `cli.py` passes
  them to `output.emit()`. This is what keeps the stdout contract testable.
- `errors.py` is the only place exit codes live.
- `backends/` are optional heavy paths; core must work without them.

## ADR Index

| ADR | Decision |
|-----|----------|
| [0001](decisions/ADR-0001-python-telethon.md) | Python 3.12 + Telethon, not Go/gotd, not TDLib-first |
| [0002](decisions/ADR-0002-cli-first-stateless.md) | Stateless CLI core; no daemons; MCP is a v1 non-goal |
| [0003](decisions/ADR-0003-output-contract.md) | stdout=data, stderr=human, fixed exit codes |
| [0004](decisions/ADR-0004-accounts-and-sessions.md) | SQLiteSession per account + file lock; import from old stack |
| [0005](decisions/ADR-0005-safety-model.md) | Reads free; writes preview→commit + audit; runtime flags not baked profiles |
| [0006](decisions/ADR-0006-media-tdlib-fallback.md) | Telethon media first, TDLib as optional fallback backend |
| [0007](decisions/ADR-0007-docs-discipline.md) | MAP + ADR + DEVLOG as mandatory agent workflow |
| [0008](decisions/ADR-0008-raw-api-passthrough.md) | `tg api` raw TL passthrough; fail-closed verb allowlist, write gate, denylist |
