# tgcli Master Plan

**Goal:** replace the daemon-first `tools/telegram` stack with a small,
stateless, gogcli-style CLI that agents and scripts can rely on.

**Why rebuild instead of refactor:** the old stack's complexity (4 MCP
daemons on ports, LaunchAgents, control-plane drift checks, plugin cache
parity, ~212k LOC) exists to police its own architecture. A stateless CLI
removes the architecture that needed policing. Domain assets (authorized
sessions, TDLib media know-how, mirror/archive) are imported or referenced,
not rewritten.

## Research Base (2026-07-06)

- **gogcli** (deepwiki): `cmd/` + `internal/`, commands per service file,
  stdout=data / stderr=human, exit code 2 for policy blocks, tokens in OS
  keyring, safety profiles baked at build time, **MCP server is an explicit
  non-goal** — agents use the CLI through a SKILL.md.
- **Telethon** (docs.telethon.dev): SQLiteSession stores auth key + entity
  cache (access_hashes) — this is what makes short-lived processes cheap:
  no re-login, no re-resolve. Concurrent access to one session file is
  unsafe → one process per account session at a time (file lock).
  `flood_sleep_threshold` auto-sleeps short FloodWaits; `client.takeout()`
  gives lower flood limits for bulk export (phase 5).
- **Old stack lessons**: 120s MCP cap killed long downloads (direct Telethon
  path in `tg download` already proved the fix); TDLib is the reliable
  backend for private-channel media; confirmed-send preview replay is worth
  keeping; everything else daemon-related is the disease, not the cure.

## Non-Goals (v1)

- MCP server (agents call `tg ... --json` via shell; revisit only with evidence).
- Mirror/archive (stays in old stack; `tg` links to it via docs, phase 6+ decision).
- Multi-user distribution / packaging for strangers.
- Bot API (this is a user-account MTProto tool).

## Phases

Each phase gets its own TDD implementation plan in `docs/superpowers/plans/`
before coding starts. A phase is done when its acceptance checks pass and
DEVLOG + MAP are updated.

### Phase 0 — Scaffold & plan  ✅ 2026-07-06
Docs-first skeleton: README, AGENTS, MAP, CONTRACT, ADR-0001…0007, DEVLOG,
phase-1 plan. Acceptance: this repo, committed.

### Phase 1 — Core + first reads
`output.py`, `errors.py`, `config.py`, `session.py`, `cli.py`,
`tg accounts list`, `tg dialogs`, `tg read`.
Acceptance: `pytest -q` green; `tg dialogs --json | jq .` works live on
account `main`; a second concurrent `tg` invocation on the same account
fails fast with exit 3 and a clear lock message, not a corrupted session.
Plan: [superpowers/plans/2026-07-06-phase-1-core-and-read.md](superpowers/plans/2026-07-06-phase-1-core-and-read.md)

### Phase 2 — Read parity with old `tg`
`tg search`, `tg count`, `tg latest`, `tg info`, `tg message`.
Acceptance: every read workflow from the old `tg` CLI has an equivalent;
side-by-side smoke on 3 real dialogs gives matching counts.

### Phase 3 — Media
`tg media download <t.me/link|chat msg_id>` via Telethon streaming
(no artificial timeout); TDLib fallback backend behind `--backend tdlib`
for private-channel cases Telethon fails on (import logic from
`tools/telegram/experiments/tdlib-media-poc`).
Acceptance: downloads a >100 MB video from a private channel to
`~/Downloads` with progress on stderr.

### Phase 4 — Write path
`tg send --preview` → stores preview in `~/.local/state/tgcli/previews/`,
`tg send --commit <preview_id>` replays it verbatim; JSONL audit log
`~/.local/state/tgcli/audit.jsonl`; `--readonly`/`TGCLI_NO_SEND` enforced
in `safety.py` before any mutating call.
Acceptance: commit-without-preview fails (exit 2); audit line written for
every send; `TGCLI_NO_SEND=1 tg send --commit ...` exits 2.

### Phase 5 — Export
`tg export messages <chat>` (takeout, JSONL out), `tg export subscribers
<channel>` (CSV). Handle `TakeoutInitDelayError` with a clear retry message.
Acceptance: exports a 10k-message dialog without FloodWait failures.

### Phase 6 — Migration & cutover
`tg accounts import` (copies authorized `.session` files from the old stack
for main/pl/recklessou/teamsyncsage); `scripts/install-link.sh` puts `tg`
on PATH ahead of the old wrapper; write `SKILL.md` for agent usage (gogcli
pattern); update `~/.claude/CLAUDE.md` Telegram routing; old daemons keep
running until 2 weeks of parallel use show no regressions, then LaunchAgents
are unloaded.
Acceptance: one normal working week where no task needed the old stack.

## Risks

| Risk | Mitigation |
|------|-----------|
| Per-invocation connect latency (1–3 s MTProto handshake) | acceptable for CLI; entity cache in session keeps it at the low end; if it ever hurts, add an opt-in local socket cache — with an ADR, not by default |
| Session file lock contention (parallel agent calls) | fail fast exit 3 + retry hint; agents serialize per account naturally |
| Telethon can't fetch some private-channel media | TDLib fallback backend (phase 3), already proven in old stack |
| FloodWait on bulk reads | `flood_sleep_threshold` for short waits, exit 5 + `retry_after` for long ones; takeout for exports |
| Scope creep back to 200k LOC | AGENTS.md: new abstraction requires ADR; YAGNI rule; MAP review each phase |
