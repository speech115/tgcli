# Project Map

Status legend: `[planned]` — not built yet, `[wip]`, `[done]`.
This file must always match the real tree (AGENTS.md rule).

```
tgcli/
├── README.md                  [done]    vision + principles
├── AGENTS.md                  [done]    agent contract, doc discipline
├── CLAUDE.md                  [done]    Claude adapter → AGENTS.md
├── pyproject.toml             [done]    uv-managed; deps: telethon; dev: pytest
├── docs/
│   ├── MAP.md                 [done]    this file
│   ├── PLAN.md                [done]    master plan, phases 0–6
│   ├── CONTRACT.md            [done]    CLI automation contract (stdout/exit codes/JSON)
│   ├── DEVLOG.md              [done]    session-by-session agent log
│   ├── FEATURES.md            [done]    TL-namespace coverage matrix (ADR-0010; trued up in phase 7)
│   ├── decisions/             [done]    ADR-0001…0010 (see index below)
│   └── superpowers/plans/     [done]    per-phase TDD implementation plans
├── src/tgcli/
│   ├── __init__.py            [done]    version string only
│   ├── cli.py                 [done]    argparse tree, global flags, dispatch, exit-code mapping
│   ├── output.py              [done]    emit(data) → stdout as JSON/plain; note()/warn() → stderr
│   ├── errors.py              [done]    TgcliError hierarchy ↔ exit codes (CONTRACT.md §4)
│   ├── config.py              [done]    ~/.config/tgcli/config.toml, accounts registry, alias resolution
│   ├── session.py             [done]    session paths, per-account file lock, TelegramClient factory
│   ├── safety.py              [done]    pre-network write gates, preview storage, JSONL audit (phase 4)
│   └── commands/
│   │   ├── accounts.py        [wip]     tg accounts list|add|import   (list in phase 1; import in 6)
│   │   ├── dialogs.py         [done]    tg dialogs                    (phase 1)
│   │   ├── read.py            [done]    tg read <chat>                (phase 1)
│   │   ├── search.py          [done]    tg search / latest / message (phase 2)
│   │   ├── info.py            [done]    tg info / count (phase 2)
│   │   ├── media.py           [planned] tg media download             (phase 3)
│   │   ├── send.py            [done]    tg send CHAT TEXT --preview / --commit (phase 4)
│   │   ├── api.py             [done]    tg api raw TL passthrough (read allowlist + audited Phase-4 writes)
│   │   └── export.py          [planned] tg export messages|subscribers (phase 5, takeout)
├── tests/                     [wip]     unit tests, mocked Telethon client
│   └── live/                  [done]    gated live smoke (TGCLI_LIVE_SMOKE=1)
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

## ADR Index

| ADR | Decision |
|-----|----------|
| [0001](decisions/ADR-0001-python-telethon.md) | Python 3.12 + Telethon, not Go/gotd, not TDLib-first |
| [0002](decisions/ADR-0002-cli-first-stateless.md) | Stateless CLI core; no daemons; MCP is a v1 non-goal |
| [0003](decisions/ADR-0003-output-contract.md) | stdout=data, stderr=human, fixed exit codes |
| [0004](decisions/ADR-0004-accounts-and-sessions.md) | SQLiteSession per account + file lock; import from old stack |
| [0005](decisions/ADR-0005-safety-model.md) | Reads free; writes preview→commit + audit; runtime flags not baked profiles |
| [0006](decisions/ADR-0006-media-tdlib-fallback.md) | ~~TDLib as optional fallback backend~~ superseded by 0009 |
| [0007](decisions/ADR-0007-docs-discipline.md) | MAP + ADR + DEVLOG as mandatory agent workflow |
| [0008](decisions/ADR-0008-raw-api-passthrough.md) | `tg api` raw TL passthrough and write-path safety; read policy superseded by ADR-0010 |
| [0009](decisions/ADR-0009-tdlib-deferred.md) | TDLib deferred: no backend in v1; phase 3 Telethon-only; evidence-gated PoC re-entry |
| [0010](decisions/ADR-0010-raw-api-read-allowlist.md) | `tg api` phase-2 explicit default-deny read allowlist |
