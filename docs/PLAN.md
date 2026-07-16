# tgcli Master Plan

> **Status: completed 2026-07-10 — historical record.** All phases 0–7 are
> done and the project is in maintenance mode. This document is kept in place
> (not archived) because MAP, README, and the knowledge base link here, and
> the Risks table below still describes live operational trade-offs. New work
> starts as a fresh scoped plan or ADR, not as a new phase in this file.

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
  path in `tg download` already proved the fix); confirmed-send preview
  replay is worth keeping; everything else daemon-related is the disease,
  not the cure. ~~TDLib is the reliable backend for private-channel media~~ —
  re-audit 2026-07-06 disproved this: the claim was never benchmarked and the
  incident was operational (ADR-0009).

## Non-Goals (v1)

- MCP server (agents call `tg ... --json` via shell; revisit only with evidence).
- Channel copy is post-v1 product work delivered as **`tg clone`**
  ([ADR-0017](decisions/ADR-0017-clone-supersedes-mirror.md),
  [clone design spec](superpowers/specs/2026-07-15-clone-design.md)). The clone
  rewrite supersedes the earlier `tg mirror` feature: mirror reached live
  parity but its implementation grew disproportionate, so clone rebuilt the
  same live-proven behavior on core primitives with hard complexity budgets.
  All clone Tasks 1–9 are complete on `feature/clone`: JSON state, status,
  preview/commit init, text and native media/album sync, mapped replies,
  protected reupload, canonical contract, controlled open/protected live
  acceptance, and final removal of the mirror parser/implementation/tests.
  Earlier mirror ADRs (0013–0016) and plans/specs remain **history**, not active
  product surface. ADR-0015 destination retention and ADR-0016 fidelity rules
  carry forward into clone; ADR-0018 records the live-found service-only tail
  correction. Post-v1 ADR-0019 adds truthful static poll snapshots, named Story
  placeholders, and reply continuity without changing the CLI surface.
  ADR-0020 makes init copy the source channel's non-empty description and static
  avatar before message sync begins. The
  independent read-only `mirror_probe.py` diagnostic remains.
- Multi-user distribution / packaging for strangers.
- Bot API (this is a user-account MTProto tool).
- Secret chats (Telethon does not implement them), voice/video calls
  (separate WebRTC media stack), account signup (ToS/ban risk) —
  see docs/FEATURES.md exclusions.

## Phases

Each phase gets its own TDD implementation plan in `docs/superpowers/plans/`
before coding starts. A phase is done when its acceptance checks pass and
DEVLOG + MAP are updated.

### Phase 0 — Scaffold & plan  ✅ 2026-07-06
Docs-first skeleton: README, AGENTS, MAP, CONTRACT, ADR-0001…0007, DEVLOG,
phase-1 plan. Acceptance: this repo, committed.

### Phase 1 — Core + first reads  ✅ 2026-07-09
`output.py`, `errors.py`, `config.py`, `session.py`, `cli.py`,
`tg accounts list`, `tg dialogs`, `tg read`.
Acceptance: `pytest -q` green; `tg dialogs --json | jq .` works live on
account `main`; a second concurrent `tg` invocation on the same account
fails fast with exit 3 and a clear lock message, not a corrupted session.
Plan: [superpowers/plans/2026-07-06-phase-1-core-and-read.md](superpowers/plans/2026-07-06-phase-1-core-and-read.md)

### Phase 2 — Read parity with old `tg` + raw passthrough (read-only)  ✅ 2026-07-10
`tg search`, `tg count`, `tg latest`, `tg info`, `tg message`;
`tg api <Namespace.method>` restricted to the explicit read allowlist (ADR-0010) —
`--write` exits 2 with a "phase 4" message until safety.py exists.
Acceptance: every read workflow from the old `tg` CLI has an equivalent;
side-by-side smoke on 3 real dialogs gives matching counts;
`tg api users.getFullUser --params '{"id": "@self"}' --json` works live;
`tg api messages.sendMessage --write ...` exits 2.

### Phase 3 — Media (Telethon-only; TDLib deferred — ADR-0009)  ✅ 2026-07-10
`tg media download <t.me/link|chat msg_id>` via Telethon streaming
(no artificial timeout, progress on stderr). Download engine borrows the
three techniques that make iyear/tdl fast — none require TDLib:
parallel chunk download (FastTelethon-style `upload.getFile` with offsets
over several connections), offset-based resume on retry (progress state in
`~/.local/state/tgcli/downloads/`), takeout sessions for bulk (phase 5).
Private `t.me/c/<id>/<msg>` links must resolve without a warm entity cache
(dialogs scan → `channels.getChannels` → exit 4 naming the account that
lacks access); `SessionRevokedError` surfaces as "needs reauth" (exit 3).
Acceptance: downloads a >100 MB video from a private channel to
`~/Downloads`; parallel chunks measurably beat single-stream on that file;
an interrupted download resumes on re-run; the 2026-07 incident case (`t.me/c/3817664407/878`) succeeds or fails diagnosably —
a reproducible Telethon failure there is the only trigger that re-opens
TDLib, as a measured PoC (ADR-0009).

Acceptance evidence (2026-07-10): `@disruptors_official` message 3609
(126,231,815 bytes) downloaded to `~/Downloads`; serial took 53 seconds and
`--parallel 4` took 22 seconds. Both outputs had SHA-256
`5ddf8830464e7f02c53bae0f796738464527472fbab432cf94346dab4e6c8506`.
An interrupted serial transfer resumed from persisted state. The incident
link returned the required exit-4 account-access diagnostic for configured
account `main`, which lacks channel access; it did not reproduce a Telethon
media failure.

### Phase 4 — Write path ✅ 2026-07-10
`tg send --preview` → stores preview in `~/.local/state/tgcli/previews/`,
`tg send --commit <preview_id>` replays it verbatim; JSONL audit log
`~/.local/state/tgcli/audit.jsonl`; `--readonly`/`TGCLI_NO_SEND` enforced
in `safety.py` before any mutating call. Previews are single-use: a second
`--commit` of the same id fails (idempotency, borrowed from b1rd33/tg-cli).
Unlock `tg api --write` (+ typed `--confirm` for destructive verbs, hard
denylist for account-lifecycle methods — ADR-0008), audited like sends.
Acceptance evidence: commit-without-preview fails (exit 2); audit lines are
written for every send and `tg api --write`; `TGCLI_NO_SEND=1 tg send --commit
...` exits 2; `tg api auth.logOut --write --confirm auth.logOut` exits 2
(denylist).

### Phase 5 — Export ✅ 2026-07-10
`tg export messages <chat>` (takeout, JSONL out), `tg export subscribers
<channel>` (CSV). Handle `TakeoutInitDelayError` with a clear retry message.
Acceptance: exports a 10k-message dialog without FloodWait failures.

### Phase 6 — Migration & cutover ✅ 2026-07-10
`tg accounts import` (copies authorized `.session` files from the old stack
for main/recklessou/teamsyncsage; `pl` was retired from migration scope on
2026-07-10 after its source session was found unauthorized); `scripts/install-link.sh` puts `tg`
on PATH ahead of the old wrapper; write `SKILL.md` for agent usage (gogcli
pattern); update `~/.claude/CLAUDE.md` Telegram routing; make tgcli the
operational default. The old daemons were decommissioned by explicit user
authorization on 2026-07-10; their plist files and sessions remain available
only for a deliberate rollback.
Acceptance evidence: local migration, PATH cutover, agent routing, and
read-only live smokes pass for `main`, `recklessou`, and `teamsyncsage`. A
parallel-use window is not required (user decision, 2026-07-10).
SKILL.md must direct agents to wrapped commands first, `tg api` last resort.

### Phase 7 — Coverage closure ✅ 2026-07-10
`docs/FEATURES.md` matrix trued up against the pinned Telethon layer;
`scripts/check-coverage.py` compares `telethon.tl.functions` namespaces to
the matrix and fails on anything unlisted. Re-run on every Telethon pin bump.
Acceptance evidence: `scripts/check-coverage.py` reports `coverage OK: 23
namespaces` against Telethon 1.44. Every TL namespace is `wrapped`, `api`,
`planned:<phase>`, or `excluded` with a reason; unknown, missing, duplicate,
and malformed classifications fail the checker.

## Risks

| Risk | Mitigation |
|------|-----------|
| Per-invocation connect latency (1–3 s MTProto handshake) | acceptable for CLI; entity cache in session keeps it at the low end; if it ever hurts, add an opt-in local socket cache — with an ADR, not by default |
| Session file lock contention (parallel agent calls) | fail fast exit 3 + retry hint; agents serialize per account naturally |
| Private-channel media failures (2026-07 incident) | root causes were operational — revoked session, cold entity cache, Telethon 1.44 parse bug; phase 3 fixes each in-code; reproducible Telethon failure re-opens TDLib via gated PoC (ADR-0009) |
| FloodWait on bulk reads | `flood_sleep_threshold` for short waits, exit 5 + `retry_after` for long ones; takeout for exports |
| Scope creep back to 200k LOC | AGENTS.md: new abstraction requires ADR; YAGNI rule; MAP review each phase |
| TL layer drift (Telegram adds methods/namespaces) | Telethon version-pinned; pin bumps re-run check-coverage against FEATURES.md (phase 7) |
| `tg api` as safety bypass | explicit default-deny read allowlist, `--write` gate wired to same env kill-switches, typed `--confirm`, hard denylist (ADR-0010) |

## Research Addendum (2026-07-06, competitor survey)

- **iyear/tdl** (Go, 7.7k★, AGPL): best-in-class media download/upload/forward
  + export; no dialog reading, no text send, no admin. Reference for phases 3/5.
- **b1rd33/tg-cli** (Python/Telethon, MIT, 1★): closest existing analog —
  62 commands, JSON envelope, exit codes, `--allow-write`, typed `--confirm`,
  idempotency keys, audit, multi-account. Decision: do not fork (bus factor 1,
  different safety model); borrow typed-confirm + single-use-preview ideas and
  use its command list as a FEATURES.md checklist.
- **telegram-mcp crowd** (chigwell, jgalea ~40 tools, dryeab, etc.): MCP niche
  is crowded; validates ADR-0002 CLI-first as the differentiator.
