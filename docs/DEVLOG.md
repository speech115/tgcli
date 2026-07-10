# DEVLOG

Append-only session log. Newest entry on top. Every agent session that
touches this repo adds one entry (AGENTS.md rule).

Template:

```markdown
## YYYY-MM-DD — <short title> (<agent/model>)
**Did:** what actually changed (files, commands, results)
**Decided:** decisions made + link to ADR if architectural
**Learned:** surprises, gotchas, dead ends worth remembering
**Next:** the single most useful next step
```

---

## 2026-07-10 — Phase 2A TSV contract hardened (Codex)
**Did:** sanitized sender names as well as message text in every four-column
message TSV path (`read`, `search`, `latest`, and `message`) and added
regression tests. Final local suite: `43 passed, 7 skipped`.
**Decided:** control characters in all untrusted human-visible fields become
spaces before TSV or default human output; JSON retains source data.
**Learned:** frozen TSV requires sanitizing every cell, not only the message
body.
**Next:** merge or hand off Phase 2A, then execute the separate read-only raw
API plan.

## 2026-07-10 — Phase 2 live smoke harness corrected (Codex)
**Did:** changed the opt-in live harness to run the installed `tg` console
script beside the active virtualenv Python, then ran the focused harness check,
one live `info me` check, the full gated live suite, and the full suite. Final
results: `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported
`7 passed in 5.84s`; `.venv/bin/pytest -q` reported
`39 passed, 7 skipped in 0.17s`.
**Decided:** the live subprocess must use the real console script and its real
tgcli state, while preserving the suite's opt-in gate.
**Learned:** the earlier exit-3 result was not an unauthorized `main` account:
the autouse test fixture set `TGCLI_STATE_DIR` to a temporary directory and
the subprocess inherited it, so it opened an empty temporary session. Removing
only that test-only environment variable lets the subprocess use the authorized
`main` session.
**Next:** proceed with the remaining Phase 2 acceptance work.

## 2026-07-10 — Phase 2 read parity contract and live smoke (Codex)
**Did:** added opt-in (`TGCLI_LIVE_SMOKE=1`) JSON-shape checks against the
explicit `main` account for `info me`, `latest me`, `count me`, and bounded
`search me tgcli-live-smoke --limit 1`; corrected the pre-existing test harness
to invoke the CLI entrypoint rather than import the module without running it.
Documented Phase 2 JSON and TSV shapes, and marked `search.py` and `info.py`
done in MAP. `.venv/bin/pytest -q` reported `39 passed, 6 skipped in 0.26s`.
**Decided:** live smoke asserts response structure and the search bound only;
it never depends on a message count or a particular Saved Message.
**Learned:** `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reached the
CLI but all six checks failed because the configured `main` session is not
authorized (exit 3, `session 'main' is not authorized`). No configuration or
session was changed; successful live validation requires an authorized `main`
session.
**Next:** authorize or provide an authorized `main` tgcli session, then rerun
the gated live suite.

## 2026-07-10 — Phase 2 read parity started (Codex)
**Did:** added one canonical message projection and exact read-by-ID support;
unit suite after the change reports `28 passed, 2 skipped`.
**Decided:** preserve the Phase 1 message JSON shape and reuse it instead of
creating a second formatter for `tg message`.
**Learned:** exact message lookup is a small read-only addition with the same
not-found contract as dialog lookup (exit 4).
**Next:** add `search`, `latest`, and CLI `message` on top of this projection.

## 2026-07-09 — Phase 2 split into read parity and raw API plans (Codex)
**Did:** reviewed the completed Phase 1 CLI, the old stack's command surface,
CONTRACT.md, FEATURES.md, and ADR-0008. Wrote two TDD execution plans:
read parity first, then the independent raw API security surface.
**Decided:** do not delay daily read workflows on the 300–500 LOC raw API
resolver. Raw API remains Phase 2 but is a separate reviewable plan with an
explicit fail-closed policy gate before request construction or a network call.
**Learned:** the old CLI's daily read set maps cleanly to `search`, `count`,
`latest`, `info`, and `message`; Phase 1 already supplies the session and
FloodWait plumbing they need.
**Next:** execute `2026-07-09-phase-2-read-parity.md`, then execute the raw
API plan and run the combined Phase 2 acceptance checks.

## 2026-07-09 — Phase 1 acceptance gates passed (Codex)
**Did:** created the local `main` tgcli configuration from the existing private
Telegram runtime variables and copied its SQLite session with SQLite's backup
API, then checked the backup integrity. Verified `26 passed, 2 skipped`, a
read-only `tg --json dialogs --limit 1` smoke (one dialog returned), and a
second invocation under an intentionally held `main.lock` (exit 3 with the
machine-readable busy error).
**Decided:** Phase 1 is accepted. The migration is deliberately minimal:
one existing account and no replacement for Phase 6 `tg accounts import`.
**Learned:** the session lock contract is observable end-to-end without making
any Telegram mutation.
**Next:** write the Phase 2 TDD plan for read parity and read-only `tg api`.

## 2026-07-09 — Phase 1 implementation complete; live gate blocked by missing config (Codex)
**Did:** implemented the remaining Phase 1 modules in commits `b227247`,
`ab33686`, `1dccd09`, `c3916b5`, and `267f258`: per-account session locking,
CLI dispatch and account listing, `dialogs`, `read`, FloodWait mapping, and a
gated live smoke suite. Added contract tests for global flags and output modes.
Final local validation: `26 passed, 2 skipped`; `tg --version` prints `0.1.0`.
**Decided:** corrected the Phase 1 CLI implementation where the plan omitted
CONTRACT.md requirements: global `--readonly`/`-v`, distinct human and TSV
output, and controlled parser-error return handling. CONTRACT.md remains law.
**Learned:** the attempted read-only live `tg dialogs --json --limit 1` smoke
exits 3 because the default tgcli config is not present; no Telegram account or
session was touched.
**Next:** provision or point `TGCLI_CONFIG` at an authorized `main` account,
then run the Phase 1 live dialog and concurrent-lock acceptance checks.

## 2026-07-06 — TDLib re-audit: fallback backend cut from plan (Claude Fable 5)
**Did:** re-audited the TDLib claim behind ADR-0006 against the old stack's
own records: its ADR (2026-06-21) had already ruled TDLib out as a runtime;
the benchmark PoC never produced RESULTS.md; the 2026-07-06 incident's root
causes were a revoked `vermassov` session, cold entity cache on `t.me/c/`
links + Telethon 1.44 parse bug, and a TDLib backend that wasn't even
installed. Wrote ADR-0009 (supersedes 0006), rewrote phase 3 as
Telethon-only with in-code fixes, updated MAP (backends/ removed), risks,
research-base line.
**Decided:** no TDLib in v1 (ADR-0009). Re-entry only via reproducible
Telethon failure on the incident case → measured, isolated PoC. Kept assets:
authorized TDLib sessions `~/.telegram-mcp-tdlib/{main,vermassov}` + PoC harness.
**Learned:** "TDLib is the reliable backend" was folklore from one manual
rescue download, promoted into our ADR without a benchmark behind it.
Re-audits of inherited claims pay off. Also: `vermassov` is missing from the
ADR-0004 import list but held the only access in the incident — revisit at
phase 6 cutover.
**Next:** execute phase-1 plan (still unchanged).
**Follow-up (same day):** user ratified cutting TDLib after a from-scratch
re-analysis (key datum: iyear/tdl, the fastest private-channel downloader,
uses gotd/td MTProto, not TDLib). Phase 3 now explicitly lists the tdl
techniques: parallel chunks (FastTelethon-style), offset resume with state
in `~/.local/state/tgcli/downloads/`, takeout for bulk (phase 5); acceptance
adds "parallel beats single-stream" check.

## 2026-07-06 — Scope grill: "all functions" resolved via raw passthrough (Claude Fable 5)
**Did:** grilled the "new version with ALL Telegram functions" request;
competitor survey (iyear/tdl 7.7k★ media-only; b1rd33/tg-cli — closest analog,
62 commands, MIT, bus-factor 1; ~10 telegram-mcp servers). Ran a loophole
cycle on the strategy until it converged (3 iterations, 6 major holes fixed).
Added ADR-0008, docs/FEATURES.md, CONTRACT §6 (tg api), PLAN updates
(non-goals, phases 2/4, new phase 7, risks, research addendum), MAP rows.
**Decided:** "all functions" = wrapped commands for daily use + `tg api`
raw TL passthrough for the long tail + FEATURES.md coverage matrix with
explicit exclusions (ADR-0008). Source of truth = pinned Telethon TL schema.
Do not fork b1rd33/tg-cli; borrow typed `--confirm` + single-use previews.
**Learned:** loopholes found by the cycle: raw passthrough would have
bypassed preview→commit (fixed: read-only until phase 4, `--write` gate);
`export*` methods look like reads but mutate (fixed: strict verb allowlist);
`auth.logOut` via passthrough would kill the managed session (fixed: hard
denylist); TL output can't obey our JSON stability rules (fixed: CONTRACT §6
exemption); secret chats/calls are impossible in Telethon (fixed: explicit
exclusions, otherwise "all functions" acceptance is unfalsifiable).
**Next:** execute phase-1 plan (unchanged by this session).

## 2026-07-06 — Phase 0: project born (Claude Fable 5)
**Did:** researched gogcli internals (deepwiki) and Telethon session/flood
semantics (context7); created docs-first scaffold: README, AGENTS, CLAUDE,
MAP, CONTRACT, PLAN, ADR-0001…0007, this DEVLOG, phase-1 TDD plan.
**Decided:** Python+Telethon over Go rewrite (ADR-0001); stateless CLI-first,
no daemons, MCP non-goal (ADR-0002); output contract with fixed exit codes
(ADR-0003); per-account SQLiteSession + file lock, import sessions from old
stack (ADR-0004); preview→commit write safety with audit log (ADR-0005);
TDLib as media fallback only (ADR-0006); MAP+ADR+DEVLOG discipline (ADR-0007).
**Learned:** gogcli has NO MCP server — it's an explicit non-goal in their
spec; agents drive it purely via CLI + SKILL.md. That validates dropping the
daemon layer entirely. Telethon's entity cache in the session file is the
key enabler for cheap short-lived processes.
**Next:** execute phase-1 plan (docs/superpowers/plans/2026-07-06-phase-1-core-and-read.md).
