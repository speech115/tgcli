# Project Map

Status legend: `[planned]` — not built yet, `[wip]`, `[done]`.
This file must always match the real tree (AGENTS.md rule).

```
tgcli/
├── README.md                  [done]    landing page: features, install, quickstart, doc index
├── CONTEXT.md                 [done]    root glossary (account/auth vocabulary; ADR-0033/0042)
├── CHANGELOG.md               [done]    released versions ↔ ADRs (semver over CONTRACT.md)
├── CONTRIBUTING.md            [done]    human-facing short form of AGENTS.md: gate, TDD, doc duties (ADR-0056)
├── SECURITY.md                [done]    private reporting channel, redaction rules, safety scope (ADR-0056)
├── LICENSE                    [done]    MIT, © speech115 (ADR-0056)
├── .github/workflows/ci.yml   [done]    CI: ruff + architecture + pyright + pytest + coverage gates (ADR-0027/0034)
├── .github/ISSUE_TEMPLATE/    [done]    bug-report + proposal forms, both labelled needs-triage (ADR-0056)
├── .github/PULL_REQUEST_TEMPLATE.md [done] gate evidence + documentation/safety checklist (ADR-0056)
├── .cursor/rules/             [done]    Cursor always-apply maintenance/TDD/docs rule
├── .claude/agents/            [done]    repo-local subagents (reviewer: independent pre-merge diff review)
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
│   ├── DEVLOG.md              [done]    closed log 1.0.0→1.2.16 + entry template (ADR-0058)
│   ├── DEVLOG-v1.md           [done]    closed log of the phases 0–7 build
│   ├── devlog/                [done]    per-session entry files YYYY-MM-DD-slug.md (ADR-0058)
│   ├── FEATURES.md            [done]    TL-namespace coverage matrix (ADR-0010; trued up in phase 7)
│   ├── guide/                 [done]    user-facing task pages, 22 + index (ADR-0041)
│   ├── assets/                [done]    README banner, dark + light SVG (no external assets)
│   ├── agents/                [done]    issue tracker, triage labels, domain-doc routing (ADR-0033), release runbook
│   ├── decisions/             [done]    ADR-0001…0057 + README.md index (ADR-0026 maintenance mode)
│   └── superpowers/           [done]    CLOSED ARCHIVE: completed plans + specs, history only
├── src/tgcli/
│   ├── __init__.py            [done]    version string only
│   ├── cli.py                 [done]    process lifecycle: preflight → execute → emit → journal
│   ├── parser.py              [done]    the argparse subparser tree; grammar only, no behaviour
│   ├── preflight.py           [done]    pre-session validation, mutation gates, preview load, api policy
│   ├── dispatch.py            [done]    network routing for one command under one open session
│   ├── output.py              [done]    emit(data) → stdout as JSON/plain; note()/warn() → stderr
│   ├── errors.py              [done]    TgcliError hierarchy ↔ exit codes (CONTRACT.md §4)
│   ├── chatref.py             [done]    chat reference normalization (numeric dialog id → int)
│   ├── config.py              [done]    ~/.config/tgcli/config.toml, accounts registry, alias resolution
│   ├── session.py             [done]    session locks (shared lock_held probe) + normal/mutation-safe TelegramClient factory
│   ├── atomic.py              [done]    atomic state/config file replacement (the only sanctioned writer)
│   ├── safety.py              [done]    pre-network write gates, preview storage, JSONL audit (phase 4)
│   ├── invocations.py         [done]    metadata-only JSONL invocation journal + fail-open writer
│   ├── confirm.py             [done]    fail-closed random_id → message-id confirmation
│   ├── formatting.py          [done]    outgoing --format {plain,md,html} → entities (ADR-0030); mask_phone (ADR-0042)
│   ├── resolve_phone.py       [done]    shared contacts.resolvePhone cooldown (ADR-0029)
│   ├── read_ops.py            [done]    typed read-operation seam shared by interactive CLI + batch (ADR-0034)
│   ├── desktop.py             [done]    osascript/open escape hatch for secrets and tg:// links (ADR-0042)
│   ├── authclient.py          [done]    unauthorized Telethon client + auth probe (ADR-0042)
│   ├── login_state.py         [done]    logins/ attempt state and session promotion (ADR-0042)
│   ├── transfer.py            [done]    striped download + parallel Save*FilePart upload, one progress cadence (ADR-0047/0049/0055)
│   ├── clone/                 [done]    clone-owned helpers (ADR-0017/0019/0020/0021/0022/0023/0045/0046/0047/0049/0054/0055)
│   │   ├── state.py           [done]    atomic JSON state, mappings, cooldown
│   │   ├── flood.py           [done]    account-scoped FloodWait cooldown + peer-created stamp + per-run wait budget (ADR-0045/0052)
│   │   ├── cooldown.py        [done]    cooldown enforcement + the `with_cooldown` RPC seam shared by init/sync/refresh (ADR-0045/0052)
│   │   ├── reupload.py        [done]    reupload transfer: still-thumb picker, upload, persistent download cache (ADR-0049/0052/0055)
│   │   ├── init_peers.py      [done]    destination shape, marker adoption, profile/avatar copy, discussion init (ADR-0020/0023/0044)
│   │   ├── ergonomics.py      [done]    mute forever + "Clone" dialog filter for tool-created peers (ADR-0046)
│   │   ├── fidelity.py        [done]    media capability classification
│   │   ├── batching.py        [done]    pure batch planner: albums, service skips
│   │   ├── transport.py       [done]    pure forward/reupload/snapshot decision
│   │   ├── pin.py             [done]    pure pin-decision + live pin-carry phase (ADR-0055)
│   │   ├── snapshot.py        [done]    truthful poll/story text rendering (+ ADR-0048 vote capture)
│   │   ├── attribution.py     [done]    source kinds, author-identity ladder, UTF-16 prefix + mention shifts; fwd_from Переслано от (ADR-0023/0050)
│   │   ├── replies.py         [done]    reply classification (ADR-0036); mapped-in-leg input rebuild
│   │   ├── quote_fallback.py  [done]    rendered quote degradation: prefix, body, stale-quote strip (ADR-0037)
│   │   ├── quotes.py          [done]    async quote resolver: native InputReplyToMessage or fallback handoff (ADR-0036/0037)
│   │   ├── reforward.py       [done]    proven-original native re-forward out of the source discussion group (ADR-0050 Part B)
│   │   ├── refresh.py         [done]    body backfill: eligibility + candidate scan for `tg clone refresh` (ADR-0054)
│   │   ├── topics.py          [done]    forum destination shape, lazy topic map, batch confirmation (ADR-0022)
│   │   ├── discussion.py      [done]    linked-chat detection, discussion group create/link/recover, anchor lookup (ADR-0023)
│   │   ├── comments.py        [done]    discussion sync leg: window-bounded by posts cursor; defer unmapped cross-leg parents (ADR-0023/0051)
│   │   ├── roster.py          [done]    best-effort source participant snapshot → JSONL sidecar (ADR-0024)
│   │   ├── progress.py        [done]    plain stderr sync progress lines: batches, phases, ~5 MB transfer marks (ADR-0049)
│   │   └── legs.py            [done]    Leg seam + WINDOW=50 posts/comments interleave constant (ADR-0023/0051)
│   └── commands/
│   │   ├── batch.py           [done]    tg batch read-only JSONL runner (ADR-0032)
│   │   ├── accounts.py        [done]    tg accounts list|import|show|remove (ADR-0042)
│   │   ├── login.py           [done]    tg accounts login QR/phone + --continue (ADR-0042)
│   │   ├── dialogs.py         [done]    tg dialogs                    (phase 1)
│   │   ├── read.py            [done]    tg read <chat>                (phase 1)
│   │   ├── search.py          [done]    tg search / latest / message (phase 2)
│   │   ├── info.py            [done]    tg info / count (phase 2)
│   │   ├── identity.py        [done]    tg resolve / contacts / mutual-chats (ADR-0029/0032)
│   │   ├── dialog.py          [done]    tg dialog pin/unpin/archive/mute (ADR-0029/0032)
│   │   ├── thread.py          [done]    tg thread reply-chain read (ADR-0029)
│   │   ├── media.py           [done]    tg media download|manifest (+ bulk download ADR-0032)
│   │   ├── send.py            [done]    tg send CHAT TEXT --preview / --commit (phase 4)
│   │   ├── draft.py           [done]    tg draft set|show|clear|list (ADR-0039)
│   │   ├── mutate.py          [done]    tg edit|delete|forward preview / commit; tg mark-read|mark-unread (ADR-0028/0029)
│   │   ├── doctor.py          [done]    tg doctor offline-first health; --connect live (ADR-0028/0040)
│   │   ├── store.py           [done]    tg store stats|cleanup; previews + logins + session_backups + clone media caches (ADR-0040/0042/0052)
│   │   ├── api.py             [done]    tg api raw TL passthrough (read allowlist + audited Phase-4 writes, ADR-0010)
│   │   ├── export.py          [done]    tg export messages|subscribers (+ incremental messages ADR-0032; broadcast walk ADR-0031)
│   │   └── clone.py           [done]    clone status/init/sync/refresh surface (ADR-0017…0025/0045/0046/0047/0048/0052/0054/0055; all live gates passed)
├── tests/                     [done]    unit tests, mocked Telethon client
│   └── live/                  [done]    gated live smoke (TGCLI_LIVE_SMOKE=1)
└── scripts/
    ├── gate.sh                [done]    full pre-commit gate: the exact CI steps, one command
    ├── install-link.sh        [done]    symlink tg → PATH (phase 6 cutover)
    ├── check-coverage.py      [done]    fail-closed Telethon namespace matrix gate (phase 7)
    ├── check-docs.py          [done]    fail-closed guide gate: flags, commands, links (ADR-0041)
    ├── bench.py               [done]    live benchmark: every command against a real account
    ├── bench-clone-state.py   [done]    offline bf-19 benchmark: JSON rewrite vs SQLite/WAL (ADR-0060)
    ├── seed_demo_channel.py   [done]    manual demo-channel seeding for clone visual acceptance
    └── check-architecture.py  [done]    module ownership + per-file line ceilings (ADR-0034)
```

## Module Ownership Rules

- `commands/*` never talk to Telethon directly for connection management —
  always through `session.client(account)` context manager.
- `commands/*` never print — they return data structures; `cli.py` passes
  them to `output.emit()`. This is what keeps the stdout contract testable.
- `errors.py` is the only place exit codes live.
- The CLI entry surface is four modules with one job each: `parser.py` says
  what can be typed, `preflight.py` says what is allowed before a session
  opens, `dispatch.py` says what runs once it is open, and `cli.py` owns the
  invocation lifecycle. Nothing but `cli.py` may emit or journal.
- `parser.py`, `preflight.py`, and `dispatch.py` inherit cli.py's read-command
  ban: read commands are reachable only through `read_ops` (ADR-0034), and
  `scripts/check-architecture.py` enforces it on all four modules.
## ADR Index

Moved to [decisions/README.md](decisions/README.md) — the canonical index
with per-ADR status (ADR-0026).
