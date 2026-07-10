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

## 2026-07-10 — Phase 6 local migration and cutover implementation (Codex)
**Did:** added pure-local `tg accounts import`: it backs up old Telethon
SQLite sessions under the same per-account lock as normal tgcli work, protects
existing warmed sessions unless `--force`, appends only missing account blocks,
and never opens Telegram. Added `scripts/install-link.sh`, then verified its
temporary-repo symlink behavior. Added root `SKILL.md`, and parser-checked all
14 documented command examples. Recorded the phase-6 TDD plan, updated MAP
and CONTRACT. Local test suite: `173 passed, 8 skipped`.
**Decided:** migration is additive and reversible at the old-stack side: it
copies sessions and credentials but does not alter daemon state or unload any
LaunchAgent. `vermassov` remains excluded because ADR-0009 records it as
revoked.
**Learned:** the phase-6 linked worktree needs its own `uv sync --locked`
environment; the root checkout's ignored `.venv` is not shared.
**Next:** run the authorized local import, read-only `pl` smoke, PATH cutover,
and update the machine-level Claude routing note; begin the parallel-use
window only if those checks pass.

## 2026-07-10 — Phases 3–5 reviewed, fixed, merged to main (Claude Fable 5 + subagents)
**Did:** orchestrated parallel Sonnet review of `codex/phase-3-media`,
`codex/phase-4-write`, `codex/phase-5-export` against PLAN.md acceptance.
Found and fixed pre-merge: Phase 3 major — uncaught `FileNotFoundError` in
`_resume_offset` when `state.json` exists without its `.part` file (4b73560);
Phase 4 blocker — case-variant denylist bypass (`auth.LogOut` resolved to
`LogOutRequest` but missed the exact-string denylist and confirm gate; fixed
by canonicalizing method names from the resolved Telethon class before all
policy checks, fail-closed, 9378459) plus uncaught `SystemExit` from `send`
usage validation. Merged all three branches sequentially with conflict
resolution in `cli.py`/docs; full suite after final merge: `165 passed,
8 skipped`. Live CLI re-check on merged main: `auth.LogOut --write` → exit 2,
`auth.logOut --write --confirm` → exit 2, `TGCLI_NO_SEND=1 send --commit` →
exit 2.
**Decided:** policy identity for `tg api` is the canonical name derived from
the resolved TLRequest class, never the raw user string.
**Learned:** uncommitted WIP (invocation journal: `invocations.py`,
`cli.py` edits, 2 test files) was sitting on main and blocked the merge;
preserved on branch `wip/invocation-journal` (df54c0a), not merged — it
references a design that was never reviewed.
**Next:** decide the fate of `wip/invocation-journal`; Phase 6 migration
only when requested. Minor review findings tracked in review notes
(CSV formula-escaping in export, audit-write try/except, progress throttling).

## 2026-07-10 — Phase 4 safe write path (Codex)
**Did:** added `safety.py` for pre-network `--readonly`, `TGCLI_READONLY=1`,
and `TGCLI_NO_SEND=1` gates; five-minute single-use JSON previews; and JSONL
audit records under `TGCLI_STATE_DIR`. Added `tg send CHAT TEXT --preview` and
`tg send --commit PREVIEW_ID`; commits replay only stored target/text. Enabled
raw `tg api --write` behind the same gate, exact destructive confirmation, the
ADR-0008 permanent denylist, and pre-dispatch audit. Added unit/CLI regressions
for all gates, preview replay/expiry, audit, confirmation, denylist, and
unknown write methods. Final command: `uv run pytest -q` → `126 passed,
8 skipped in 0.33s`; no Telegram mutation was performed.
**Decided:** previews are consumed before network dispatch, so a failed send
cannot be retried with the same id; this preserves the single-use safety
contract and leaves a local audit record for every authorised attempt.
**Learned:** raw API policy must resolve an allowed write method before config
or session acquisition; otherwise a typo can create an unnecessary Telegram
connection despite being invalid.
**Next:** review the Phase 4 diff and commit it on `codex/phase-4-write` when
the user requests a commit.

## 2026-07-10 — Phase 3 media implementation (Codex)
**Did:** implemented `tg media download` with public/private link parsing,
private-channel dialog scanning plus `channels.getChannels` validation, safe
output paths, resumable serial Telethon streaming, opt-in offset/stride
parallel transfer, stderr progress, and clear revoked-session reauth errors.
Added 19 media/CLI tests and two session-revocation regressions. Final local
suite: `129 passed, 8 skipped`; the existing gated live read suite passed
`8 passed`. The incident link returned the expected exit-4 account-access
diagnostic for configured account `main`.
**Decided:** existing completed output files are never overwritten (exit 2).
Serial transfers resume via state under `~/.local/state/tgcli/downloads/`;
parallel transfers start fresh and reject a partial serial state.
**Learned:** Telethon's `iter_download` directly supports the offset and
stride control required for resume and parallel chunks, so Phase 3 needs no
TDLib or additional dependency.
**Acceptance:** downloaded 126,231,815-byte public media to `~/Downloads`;
serial took 53 seconds and `--parallel 4` took 22 seconds. The serial and
parallel files had the same SHA-256
`5ddf8830464e7f02c53bae0f796738464527472fbab432cf94346dab4e6c8506`.
An interrupted serial transfer resumed successfully. The incident link
`t.me/c/3817664407/878` returned the specified exit-4 `main`-lacks-access
diagnostic, not a Telethon media failure.
**Next:** begin Phase 4 write safety only when requested.

## 2026-07-10 — Phase 5 live acceptance passed (Codex)
**Did:** selected public `@msk7days` after `tg count` returned `14,296`, then
exported it through account `main` to
`/Users/sereja/Downloads/tgcli-phase5-msk7days-2026-07-10.jsonl`. The atomic
destination contains `14,296` valid JSONL records, ordered from id `1` to
`16058`; no FloodWait occurred. The live run exposed a legacy malformed
SQLite `takeout_id` value (`b''`), so export now reuses valid integer takeout
ids and clears malformed values before initializing a new takeout. Added two
regressions for both behaviours.
**Decided:** Phase 5 is accepted. The malformed-ID repair is local session
compatibility handling, not a new architecture, so no ADR is required.
**Learned:** a copied Telegram session can carry a non-integer stale takeout
identifier that passes connection/auth checks but fails only while Telethon
serializes `InvokeWithTakeoutRequest`.
**Next:** proceed to the next explicitly requested phase.

## 2026-07-10 — Phase 5 completion audit strengthened (Codex)
**Did:** added the 10k-message local takeout regression, asserting all 10,000
JSONL records are written oldest-first and the completion summary reports the
same count. Focused export tests report `9 passed in 0.19s`; the full local
suite reports `117 passed, 8 skipped in 0.37s`.
**Decided:** the simulation proves the full streaming command path at the
acceptance cardinality, but does not replace the required real Telegram
takeout evidence.
**Learned:** the remaining live gate cannot be inferred from unit tests: it
requires a deliberately supplied non-sensitive 10k+ dialog and authorized
account, because selecting one automatically could export private content.
**Next:** run the designated live export, record its count and FloodWait result,
then mark Phase 5 accepted only if it succeeds.

## 2026-07-10 — Phase 5 export implementation (Codex)
**Did:** added `tg export messages <chat> --output PATH` (streaming Telethon
takeout JSONL, oldest first) and `tg export subscribers <channel> --output
PATH` (streaming quoted CSV). Both commands use an atomic sibling temporary
file, return a normal completion summary, and leave an existing destination
unchanged when the export fails. Added eight focused export tests covering
takeout, JSONL order, CSV headers/quoting, empty exports, not-found, atomic
failure, `TakeoutInitDelayError`, and the no-default-timeout contract;
`uv run pytest -q` reported `116 passed, 8 skipped`.
**Decided:** record files require explicit `--output`, while stdout retains
the one-document JSON/TSV contract as a completion summary. No ADR was needed:
this adds a phase-planned command without changing the architecture.
**Learned:** the source checkout's existing `.venv` is installed editable for
that checkout, so the isolated worktree must use its own `uv run` environment
to test its changed `src/` tree. The normal 60-second deadline would invalidate
the 10k-message acceptance criterion, so exports intentionally have no default
overall timeout while an explicit `--timeout` remains available.
**Next:** run the 10k-message live export only after a safe designated dialog
and authorized account are supplied; do not select a private dialog by guess.

## 2026-07-10 — Raw API read allowlist expanded to 35 methods (Claude Fable 5 + subagents)
**Did:** expanded `READ_METHOD_ALLOWLIST` in `src/tgcli/commands/api.py` from
`users.getFullUser` to the 35 batch-reviewed read methods across messages
(18), channels (7), users (2), contacts (3), photos (1), and stats (4). TDD:
red run of the new parametrized coverage reported `35 failed, 13 passed`;
after the one-constant change the full suite reported `108 passed, 8 skipped`.
Every allowlisted name is asserted to resolve to a real TLRequest of pinned
Telethon 1.44 (catches typos), and five rejected read-looking methods
(`messages.getMessagesViews`, `contacts.getLocated`, `contacts.resolvePhone`,
`messages.getExportedChatInvites`, `messages.getBotCallbackAnswer`) are
regression-tested to exit 2 before config loading. Updated ADR-0010 (full
list + "Reviewed and rejected" table), CONTRACT §6, and FEATURES rows.
**Decided:** the 2026-07-10 batch review is the second ADR-0010 allowlist
revision; default-deny stands, and "new method = ADR update + regression
test" remains the only path in. auth.* and account.* stay excluded wholesale.
**Learned:** the resolve-to-TLRequest test is the cheap safety net for batch
allowlist edits — a misspelled method would otherwise pass policy tests and
only fail at dispatch time.
**Next:** live-check comment counting via `messages.getReplies`/discussion
methods, then merge the branch.

## 2026-07-10 — Phase 2 accepted: side-by-side parity smoke passed (Claude Fable 5 + subagents)
**Did:** ran the Phase 2 acceptance smoke: old daemon-stack `tg` CLI vs new
tgcli, side by side on 3 real dialogs. Counts, latest message ids, and message
text all match 3/3: Saved Messages (count 1374, latest 280484), @karlobrans
channel (75, 965 — text byte-identical), @totwtop (1249, 280271). Cross-check:
`message` lookup by id from the new CLI returns identical content in the old
CLI. Marked Phase 2 done in PLAN.md.
**Decided:** Phase 2 is accepted; branch is ready to merge to main.
**Learned:** the acceptance run itself surfaced two old-stack defects: the
main daemon's read lanes sat in circuit_open for ~25 minutes, and @poremido
fails all old-stack message lanes with a reproducible Telethon "Could not find
a matching Constructor ID" error while new tgcli reads the same chat fine
(count 354, latest 280253). The comparison baseline was flakier than the thing
under test — which is the reason this project exists.
**Next:** merge phases 1–2 to main, then execute Phase 3 (media downloads).

## 2026-07-10 — Default-deny raw API read policy (Codex)
**Did:** replaced the raw method-name prefix heuristic with the reviewed,
explicit Phase-2 allowlist `users.getFullUser`; added regression coverage that
`auth.checkPassword` and `account.getTmpPassword` exit 2 before config loading
or session acquisition; and recorded the policy in ADR-0010, CONTRACT, and
MAP. TDD red run: `.venv/bin/pytest tests/test_cli_api_policy.py -q` reported
`2 failed, 5 passed` because both methods tried to load config. Green focused
run reported `7 passed in 0.14s`; full suite reported `67 passed, 8 skipped in
0.25s`; `TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported `8 passed
in 6.99s`.
**Decided:** no raw TL method is classified as read-only by its name. Phase 2
permits only ADR-0010's explicit allowlist; all other methods fail closed.
**Learned:** TL names such as `checkPassword` and `getTmpPassword` can hide
credential-sensitive operations, so verb prefixes are not a safety boundary.
**Next:** add further raw methods only through a reviewed ADR-0010 allowlist
update with dispatcher and no-session policy regressions.

## 2026-07-10 — Read-only raw API CLI passthrough (Codex)
**Did:** wired allowlisted `tg api` calls through the normal session context,
added CLI envelope/FloodWait tests, a gated live
`users.getFullUser --params '{"id":"@self"}'` check, and a no-session
`messages.sendMessage --write` policy regression. The live check exposed a
stale entity-cache edge case for `@self`; it now maps directly to
`InputUserSelf` for an input-user field. Final local checks:
`.venv/bin/pytest tests/test_api_conversion.py tests/test_cli_api.py -q`
reported `17 passed in 0.14s`; `.venv/bin/pytest -q` reported
`65 passed, 8 skipped in 0.23s`; and
`TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` reported
`8 passed in 7.80s`.
**Decided:** raw API writes remain unavailable in phase 2: any non-allowlisted
method and any `--write` request exits 2 before session acquisition; phase 4
owns enabling audited writes under ADR-0008.
**Learned:** `get_input_entity("@self")` may use a cached non-user peer, so a
known self-user input must not rely on generic entity-cache coercion.
**Next:** complete the remaining Phase 2 acceptance review or proceed to Phase
3 media work.

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
## 2026-07-10 — Phase 3 design approved (Codex)
**Did:** created an isolated `codex/phase-3-media` worktree, restored the
locked uv environment, and recorded the Telethon-only media-download design.
Baseline in the isolated worktree: `108 passed, 8 skipped`.
**Decided:** final media files never overwrite existing paths; interrupted
downloads resume from state under `~/.local/state/tgcli/downloads/`.
**Learned:** the source checkout has an unrelated untracked invocation test,
so all Phase 3 work remains in the separate worktree.
**Next:** review this design, write the TDD implementation plan, then start
the first failing media-command test.
