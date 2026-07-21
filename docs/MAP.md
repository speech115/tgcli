# Project Map

Status legend: `[planned]` — not built yet, `[wip]`, `[done]`.
This file must always match the real tree (AGENTS.md rule).

```
tgcli/
├── README.md                  [done]    vision + principles
├── .github/workflows/ci.yml   [done]    CI: ruff + pyright + pytest + coverage gate on push/PR (ADR-0027)
├── AGENTS.md                  [done]    agent contract, doc discipline
├── CLAUDE.md                  [done]    Claude adapter → AGENTS.md
├── SKILL.md                   [done]    agent command routing and safety contract (phase 6)
├── pyproject.toml             [done]    uv-managed; telethon==1.44.0; dev: pytest, ruff, pyright
├── docs/
│   ├── MAP.md                 [done]    this file
│   ├── PLAN.md                [done]    completed master plan (historical; current scope → ISSUES.md)
│   ├── CLONE.md               [done]    tg clone chronicle: capability, history, acceptance status (ADR-0026)
│   ├── CONTRACT.md            [done]    CLI automation contract (stdout/exit codes/JSON)
│   ├── ISSUES.md              [done]    deliberately deferred product work and re-entry gates
│   ├── PROPOSALS.md           [done]    unvetted owner wishlist backlog (2026-07-21); each item needs owner+ADR
│   ├── DEVLOG.md              [done]    session-by-session agent log
│   ├── FEATURES.md            [done]    TL-namespace coverage matrix (ADR-0010; trued up in phase 7)
│   ├── decisions/             [done]    ADR-0001…0029 + README.md index (ADR-0026 maintenance mode)
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
│   ├── confirm.py             [done]    fail-closed random_id → message-id confirmation
│   ├── clone/                 [done]    clone-owned helpers (ADR-0017/0019/0020/0021/0022/0023)
│   │   ├── state.py           [done]    atomic JSON state, mappings, cooldown
│   │   ├── fidelity.py        [done]    media capability classification
│   │   ├── batching.py        [done]    pure batch planner: albums, service skips
│   │   ├── transport.py       [done]    pure forward/reupload/snapshot decision
│   │   ├── snapshot.py        [done]    truthful poll/story text rendering
│   │   ├── attribution.py     [done]    source kinds, author-identity ladder, UTF-16 prefix + mention shifts (ADR-0023)
│   │   ├── replies.py         [done]    validated reply mapping and explicit flatten fallback
│   │   ├── topics.py          [done]    forum destination shape, lazy topic map, batch confirmation (ADR-0022)
│   │   ├── discussion.py      [done]    linked-chat detection, discussion group create/link/recover, anchor lookup (ADR-0023)
│   │   ├── comments.py        [done]    phase-2 sync leg: copies the discussion group, remaps comment threads (ADR-0023)
│   │   ├── roster.py          [done]    best-effort source participant snapshot → JSONL sidecar (ADR-0024)
│   │   └── legs.py            [done]    Leg seam sharing the batch path between the posts and discussion legs (ADR-0023)
│   └── commands/
│   │   ├── accounts.py        [done]    tg accounts list|import      (phase 1/6; SQLite backup migration)
│   │   ├── dialogs.py         [done]    tg dialogs                    (phase 1)
│   │   ├── read.py            [done]    tg read <chat>                (phase 1)
│   │   ├── search.py          [done]    tg search / latest / message (phase 2)
│   │   ├── info.py            [done]    tg info / count (phase 2)
│   │   ├── identity.py        [done]    tg resolve / contacts (peer discovery)
│   │   ├── media.py           [done]    tg media download             (phase 3; Telethon-only)
│   │   ├── send.py            [done]    tg send CHAT TEXT --preview / --commit (phase 4)
│   │   ├── mutate.py          [done]    tg edit|delete|forward preview / commit; tg mark-read (ADR-0028)
│   │   ├── doctor.py          [done]    tg doctor environment/session health report (ADR-0028)
│   │   ├── api.py             [done]    tg api raw TL passthrough (read allowlist + audited Phase-4 writes, ADR-0010)
│   │   ├── export.py          [done]    tg export messages|subscribers (phase 5, takeout)
│   │   └── clone.py           [done]    clone status/init/sync surface (ADR-0017…0025; all live gates passed)
├── tests/                     [done]    unit tests, mocked Telethon client
│   └── live/                  [done]    gated live smoke (TGCLI_LIVE_SMOKE=1)
└── scripts/
    ├── install-link.sh        [done]    symlink tg → PATH (phase 6 cutover)
    ├── check-coverage.py      [done]    fail-closed Telethon namespace matrix gate (phase 7)
    ├── bench.py               [done]    live benchmark: every command against a real account
    └── seed_demo_channel.py   [done]    manual demo-channel seeding for clone visual acceptance
```

## Module Ownership Rules

- `commands/*` never talk to Telethon directly for connection management —
  always through `session.client(account)` context manager.
- `commands/*` never print — they return data structures; `cli.py` passes
  them to `output.emit()`. This is what keeps the stdout contract testable.
- `errors.py` is the only place exit codes live.
## ADR Index

Moved to [decisions/README.md](decisions/README.md) — the canonical index
with per-ADR status (ADR-0026).
