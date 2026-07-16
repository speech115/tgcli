# Project Map

Status legend: `[planned]` — not built yet, `[wip]`, `[done]`.
This file must always match the real tree (AGENTS.md rule).

```
tgcli/
├── README.md                  [done]    vision + principles
├── .github/workflows/ci.yml   [done]    CI: pytest + coverage gate on push/PR
├── AGENTS.md                  [done]    agent contract, doc discipline
├── CLAUDE.md                  [done]    Claude adapter → AGENTS.md
├── SKILL.md                   [done]    agent command routing and safety contract (phase 6)
├── pyproject.toml             [done]    uv-managed; telethon==1.44.0; dev: pytest
├── docs/
│   ├── MAP.md                 [done]    this file
│   ├── PLAN.md                [done]    master plan, phases 0–7 (all complete)
│   ├── CONTRACT.md            [done]    CLI automation contract (stdout/exit codes/JSON)
│   ├── ISSUES.md              [done]    deliberately deferred product work and re-entry gates
│   ├── DEVLOG.md              [done]    session-by-session agent log
│   ├── FEATURES.md            [done]    TL-namespace coverage matrix (ADR-0010; trued up in phase 7)
│   ├── decisions/             [done]    ADR-0001…0021; ADR-0017 clone supersedes mirror
│   └── superpowers/plans/     [done]    completed v1 plans; mirror plans superseded by clone spec (ADR-0017)
├── src/tgcli/
│   ├── __init__.py            [done]    version string only
│   ├── cli.py                 [done]    argparse tree, global flags, dispatch, exit-code mapping
│   ├── output.py              [done]    emit(data) → stdout as JSON/plain; note()/warn() → stderr
│   ├── errors.py              [done]    TgcliError hierarchy ↔ exit codes (CONTRACT.md §4)
│   ├── chatref.py             [done]    chat reference normalization (numeric dialog id → int)
│   ├── config.py              [done]    ~/.config/tgcli/config.toml, accounts registry, alias resolution
│   ├── session.py             [done]    session locks + normal/mutation-safe TelegramClient factory
│   ├── safety.py              [done]    pre-network write gates, preview storage, JSONL audit (phase 4)
│   ├── invocations.py         [done]    metadata-only JSONL invocation journal + fail-open writer
│   ├── mirror_probe.py        [done]    independent read-only protected-content diagnostic
│   ├── clone/                 [done]    clone-owned helpers (ADR-0017/0019/0020/0021)
│   │   ├── state.py           [done]    atomic JSON state, mappings, cooldown
│   │   ├── fidelity.py        [done]    truthful poll snapshots and named Story placeholders
│   │   ├── profile.py         [done]    init-time chat description/bio and static avatar copy
│   │   ├── attribution.py     [done]    source kinds, author cache, UTF-16 prefix shifts
│   │   └── replies.py         [done]    validated reply mapping and explicit flatten fallback
│   └── commands/
│   │   ├── accounts.py        [done]    tg accounts list|import      (phase 1/6; SQLite backup migration)
│   │   ├── dialogs.py         [done]    tg dialogs                    (phase 1)
│   │   ├── read.py            [done]    tg read <chat>                (phase 1)
│   │   ├── search.py          [done]    tg search / latest / message (phase 2)
│   │   ├── info.py            [done]    tg info / count (phase 2)
│   │   ├── media.py           [done]    tg media download             (phase 3; Telethon-only)
│   │   ├── send.py            [done]    tg send CHAT TEXT --preview / --commit (phase 4)
│   │   ├── api.py             [done]    tg api raw TL passthrough (read allowlist + audited Phase-4 writes, ADR-0010)
│   │   ├── export.py          [done]    tg export messages|subscribers (phase 5, takeout)
│   │   └── clone.py           [done]    live-accepted clone status/init/sync surface (ADR-0017/0018/0021)
├── tests/                     [done]    unit tests, mocked Telethon client
│   └── live/                  [done]    gated live smoke (TGCLI_LIVE_SMOKE=1)
└── scripts/
    ├── install-link.sh        [done]    symlink tg → PATH (phase 6 cutover)
    ├── check-coverage.py      [done]    fail-closed Telethon namespace matrix gate (phase 7)
    ├── bench.py               [done]    live benchmark: every command against a real account
    ├── mirror_probe.py        [done]    operator entrypoint for the retained read-only diagnostic
    └── seed_demo_channel.py   [done]    manual demo-channel seeding for clone visual acceptance
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
| [0011](decisions/ADR-0011-audit-write-failure-policy.md) | Audit persistence fails closed before any mutation |
| [0012](decisions/ADR-0012-invocation-journal-and-verbose-diagnostics.md) | Local invocation journal and opt-in stderr diagnostics |
| [0013](decisions/ADR-0013-channel-mirror.md) | Superseded crash-safe mirror research design and R0 evidence |
| [0014](decisions/ADR-0014-lean-faithful-mirror.md) | Lean faithful channel mirror; supersedes ADR-0013 production architecture |
| [0015](decisions/ADR-0015-truthful-persistent-mirror-showcase.md) | Production-path-only persistent private showcase and topology promotion gates |
| [0016](decisions/ADR-0016-live-mirror-fidelity-corrections.md) | Service-message skip, reply reconstruction fallback, and append-only TSV extension |
| [0017](decisions/ADR-0017-clone-supersedes-mirror.md) | Clone rewrite supersedes mirror; JSON state, core-primitive reuse, tail-verification crash model, complexity budgets |
| [0018](decisions/ADR-0018-clone-service-tail.md) | Clone tail verification accepts service-only rows while still blocking ordinary destination content |
| [0019](decisions/ADR-0019-clone-truthful-fallbacks.md) | Poll snapshots, named Story placeholders, nested replies, and missing-parent continuity |
| [0020](decisions/ADR-0020-clone-channel-profile.md) | Init-time channel description and static avatar copy with retry-safe recovery |
| [0021](decisions/ADR-0021-clone-attributed-sources.md) | Megagroup/dialog sources, hybrid attribution transport, and explicit reply flatten reporting |
