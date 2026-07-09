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
