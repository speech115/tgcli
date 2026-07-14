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

## 2026-07-14 — Truthful persistent mirror showcase planned (Codex GPT-5)
**Did:** audited the accepted lean product branch, the expanded R1 laboratory,
and the rejected visual-runner prototype. Confirmed that production
`mirror init` plus idempotent text `mirror sync` already exists, while expanded
comment/forum/supergroup canaries seed both sides independently. Added
ADR-0015 and a TDD plan that hardens ambiguous destination creation and durable
account-level FloodWait cooldown before any further live showcase mutation.
**Decided:** a showcase is a retained private destination created and filled
only by production mirror commands. It is not a cleanup obligation and there is
no second mutating showcase runner. Promotion is per topology through
`candidate`, `organic_copy_green`, `visual_approved`, and
`production_enabled`; `channel_forum` remains blocked by Telegram evidence.
**Learned:** current lean init scans an exact marker but does not persist a
pre-dispatch creation state. An accepted create followed by a lost response and
temporary zero-result scan could therefore create a duplicate. This safety gap
must close before the native media/album/reply slice or a live run.
**Checks:** isolated worktree baseline: `255 passed, 8 skipped`; namespace
coverage: `coverage OK: 23 namespaces`; no Telegram mutation performed.
**Next:** execute `2026-07-14-mirror-init-safety.md` through TDD and independent
task review, then implement native media, albums, and mapped replies.

## 2026-07-13 — Lean mirror final Telegram edge review (Codex)
**Did:** added narrow regressions and fixes for all whole-branch review
findings: `UpdateShort` confirmation envelopes, active secondary public
usernames, resumable cancellation, no implicit timeout for backfill, and the
actual resolved destination title in sync output.
Final verification after the fixes: mirror-focused `41 passed`; full suite
`255 passed, 8 skipped`; `coverage OK: 23 namespaces`; clean
`git diff --check`; `tg mirror --help` exposes only `init` and `sync`.
**Decided:** `mirror sync` follows export's long-running timeout policy while
`mirror init` keeps the normal 60-second default. A destination is private only
when it has neither a primary username nor any active secondary username.
**Learned:** Telegram's valid update envelope and username shapes are wider
than the most common high-level objects; raw request paths need tests for the
full pinned union, not only the usual response.
**Next:** rerun focused/full gates and publish the reviewed draft PR.


## 2026-07-13 — Lean mirror init and text sync implemented (Codex)
**Did:** implemented the first ADR-0014 product slice on
`codex/mirror-lean-product`: per-source SQLite identity/state, persisted signed
64-bit copy random ids, atomic mapping/cursor confirmation, non-mutating
`mirror init` preview, resumable private destination creation, and idempotent
unprotected text `mirror sync`. All Telegram behavior is covered with mocked
clients; no live account or chat was mutated.
TDD evidence: store RED was the expected missing-module error and GREEN was
`6 passed`; init RED was seven missing-command/module failures and GREEN was
`7 passed`; sync RED began with the missing subcommand and closed at `11 passed`
after review regressions. Final gates: focused mirror slice `31 passed`, full
suite `245 passed, 8 skipped`, `coverage OK: 23 namespaces`, clean
`git diff --check`, and `tg mirror --help` listed only `init` and `sync`.
**Decided:** ambiguous destination creation uses a short deterministic temporary
marker independent of source title, then restores the current source title.
Both channel-level and message-level forwarding protection stop this slice
before destination writes; protected reconstruction remains later work.
**Learned:** independent task review caught two practical edge cases before
live use: Telegram title length and per-message `noforwards`. The store and CLI
were corrected with regressions rather than weakening the contract.
**Next:** open a draft PR, then implement media/albums/replies as the next
vertical slice before linked comments/watch.


## 2026-07-13 — Block protected messages before mirror sync writes (Codex)
**Did:** fixed `mirror sync` so a per-message `noforwards=True` stops both new
history and pending replay before prepare/audit/forward/confirmation. Added
regressions for both paths. RED was the new-history regression copying one
protected message (`expected exit 2, got 0`); focused GREEN was `11 passed in
0.20s`. The requested mirror regression set passed with `31 passed in 0.28s`,
and the full suite passed with `245 passed, 8 skipped in 1.68s`.
**Decided:** keep protected reconstruction in its later product slice; this fix
only closes the native-forward safety gap and does not change the CLI contract.
**Learned:** channel-level `noforwards` is insufficient because fetched
messages can carry the protection flag independently, including pending replay.
**Next:** re-review Task 3 against the two per-message protection regressions.

## 2026-07-13 — Mirror product reset to a lean vertical slice (Codex)
**Did:** reviewed the clean `main` baseline, the expanded laboratory branch and
draft PR, ADR-0013, its M0-M4 plan, and an independent review of their cost.
Recorded ADR-0014 and a TDD plan for the first product slice: destination init
plus restart-safe text sync. Baseline verification before branching was
`uv run pytest -q` (`214 passed, 8 skipped`) and
`uv run python scripts/check-coverage.py` (`coverage OK: 23 namespaces`).
**Decided:** keep the expanded lab PR as a research artifact and do not merge it
into the product path. Preserve the useful R0 protected-content evidence and
the persisted-random-id invariant, while dropping the five-state outbox,
forensic deletion proof, and broad topology matrix from the shipping critical
path.
**Learned:** the cheapest meaningful restart guarantee is a stable Telegram
random id persisted before dispatch plus atomic mapping/cursor confirmation;
the larger lab protocol is not required to begin validating fidelity.
**Next:** execute `2026-07-13-lean-mirror-init-sync.md` with mocked Telegram,
then review before any controlled live mutation.

## 2026-07-11 — R0 protected-content probe evidence (Claude Opus 4.8)
**Did:** ran the read-only mirror capability probe (`scripts/mirror_probe.py`,
commit 7de7c90) live against two real protected broadcast channels — one where
the account is owner, one where it is an ordinary subscriber. Runtime: Telegram
layer 227, Telethon 1.44.0. Both sources reported `protected: true`. Owned scan
covered 86 messages; subscriber scan covered 2394. The paired privacy validator
passed and `audit.jsonl` was byte-for-byte unchanged (no mutation).
**Decided:** R0 Decision Gate → **Branch 1**. Every discovered byte-bearing kind
returned Telethon `pass` for both account roles, so gotd is NOT required. The
next step is an R1 controlled-lab plan to fill `not_found`/`limited` kinds, not a
second backend.
**Learned:** Telethon streamed complete bytes (full SHA-256) for every media kind
in both roles, including a ~1.0 GB video read as an ordinary subscriber of a
`noforwards` channel — strong evidence the four-tier capability router collapses
to native-copy (unprotected) + Telethon-reconstruction (protected). Byte-`pass`
kinds: owned = photo, video, video_note; subscriber = audio, document, photo,
sticker, video, video_note, voice. `not_applicable` (no byte payload): text,
webpage, service, poll, story. Zero `fail`/`inconclusive`. Gotcha: an earlier
Codex run mis-selected an unprotected channel as the "owned protected" source,
which is why its paired validator failed; the real owned protected channel was
used here.
**Next:** consolidate ADR-0013 + the router design + the M0-M4 plan into one
gotd-free decision, then write the R1 controlled-lab plan.

## 2026-07-11 — Telegram message-type probe inventory expanded (Codex GPT-5)
**Did:** compared the current official Telegram Message/MessageMedia schema with
the pinned Telethon 1.44 constructors. No mirror plan or production code changed.
**Decided:** use a tiered probe matrix: P0 core content and structure, P1
interactive media, P2 transactional/service/edge cases. Test all document
subtypes separately even though TL represents them under MessageMediaDocument.
**Learned:** the pinned layer exposes 18 MessageMedia constructors and 58
MessageAction constructors; the current Telegram schema additionally lists
MessageMediaVideoStream, so new-layer/unsupported behavior needs an explicit
probe result instead of silent omission.
**Next:** freeze paid-media policy, then approve the complete read-only probe
design before writing or running it.

## 2026-07-11 — Native Telegram mirror simplification identified (Codex GPT-5)
**Did:** checked the proposed mirror architecture against the pinned Telethon
1.44 client/source and current official Telegram MTProto documentation. No plan
or production code was changed.
**Decided:** test a native-first path before executing the current M0-M4 plan:
raw `messages.forwardMessages(drop_author=True)` with persisted batch random ids,
plus Telethon `catch_up=True` for watcher gap recovery. Keep download/reupload out
of the primary path unless a protected-content fallback is explicitly chosen.
**Learned:** native server-side copy can remove most media rendering and temp-file
state for unprotected public/private channels. Official content protection
explicitly rejects forwarding/copying with `CHAT_FORWARDS_RESTRICTED`, so a
simple official design cannot promise protected-channel copying.
**Next:** run a disposable source/destination matrix probe before revising
ADR-0013 and the M0-M4 plan again.

## 2026-07-11 — Mirror ADR and plan made crash-safe (Codex GPT-5)
**Did:** rewrote proposed ADR-0013 and the M0-M4 implementation plan to address
the blocking review findings. Added a dedicated watcher-session topology,
peer-scoped ledger keys, durable random-id outbox transitions, resumable channel
creation, race-free watch startup, targeted deletion confirmation, and an
executable protected-content probe. Synchronized MAP, FEATURES, and historical
PLAN pointers; no production mirror code or Telegram state was changed.
**Decided:** M0 now has two hard gates: concurrent primary/watcher session proof
and a protected-content capability matrix. Implementation cannot begin until
both are green. Ambiguous sends must recover through the persisted random id;
ledger membership alone is not accepted as idempotency.
**Learned:** Telethon's high-level send helpers do not expose a caller-supplied
random id, while raw SendMessage/SendMedia/SendMultiMedia requests do; the plan
therefore freezes raw durable dispatch after upload.
**Next:** review/accept ADR-0013, then execute M0 only; stop before destination
creation unless both live evidence gates pass.

## 2026-07-11 — Mirror ADR and implementation plan reviewed (Codex GPT-5)
**Did:** reviewed untracked ADR-0013 and the M0-M4 plan against MAP, PLAN,
CONTRACT, FEATURES, ADR-0002/0005/0009, and the current session/safety/media/
export implementations. No mirror source code or proposed document was changed.
**Decided:** implementation is blocked pending a crash-consistency protocol,
race-free watcher startup, peer-scoped ledger keys, deletion-confirmation rules,
and an explicit answer for the exclusive session lock held by `watch`.
**Learned:** the current plan would make every other command for the watched
account fail busy; `tg api upload.getFile` is not available through the current
raw contract; audit state also contradicts the plan's `mirrors/`-only rule.
**Next:** revise ADR-0013 and the plan around session topology and a durable
`pending -> dispatched -> confirmed` operation state before starting M0/M1.

---

## 2026-07-11 — Bench default retargeted; PLAN.md marked historical (Claude Fable 5)
**Did:** the dr34m.txt channel was renamed to "MIR Сергея Иванова"
(@mir_ivanova) and is now reachable from the main account, so
`scripts/bench.py` defaults its subscribers step to `@mir_ivanova` instead of
the mirror-channel id (verified live: 20-row CSV export, exit 0). Added a
completed/historical status banner to docs/PLAN.md.
**Decided:** PLAN.md stays at its current path as a historical record — MAP,
README, and kb notes link to it and its Risks table is still operationally
current; new work gets a fresh scoped plan or ADR, never a new phase there.
**Next:** none; maintenance mode.

## 2026-07-11 — v1 closeout: PRs merged, CI, live bench, numeric-id fix (Claude Fable 5)
**Did:** reviewed and merged PR #2 (hardening + invocation diagnostics) and
PR #3 (legacy MCP decommission record, DEVLOG conflict resolved); deleted all
stale branches and worktrees (only `main` remains); added GitHub Actions CI
(`pytest` + coverage gate, green in 16 s); set repo description/topics; docs
truth-up (MAP phases 0–7 + ADR index 0011–0012, README v1-complete status,
DEVLOG chronology repaired). Built `scripts/bench.py`: live 13-step benchmark
of every command on one account (~20 s), JSON on stdout, table on stderr,
takeout delays SKIP. First run failed `export subscribers <numeric id>` —
every wrapped command passed digit strings straight to `get_entity()`, which
treats them as phone numbers. Fixed via `chatref.parse()` in all seven
resolution sites (TDD: unit + CLI regressions). Final: 198 unit tests pass,
live bench 13/13 PASS.
**Decided:** branch protection stays off — GitHub free plan rejects it on
private repos (403); revisit if the repo goes public or plan upgrades. Bench
defaults subscribers export to `mirror: dr34m.txt` (-1003890108644) because
the main account is not a member of the public dr34m.txt channel.
**Learned:** the live bench paid for itself on the first run: unit tests with
fakes could not catch Telethon's phone-number interpretation of digit strings.
**Next:** run `scripts/bench.py` after any Telethon pin bump alongside
`check-coverage.py`.

## 2026-07-11 — Preserve cancellation cleanup regression (Codex)
**Did:** recovered the one unique untracked regression from an obsolete Claude
worktree: cancellation during an atomic export removes its temporary file.
The production cleanup behavior was already present in the hardening branch.
**Decided:** retain the test in the active PR rather than duplicate its older
source changes or publish the stale worktree.
**Next:** run the full suite, update the PR, then remove only verified stale
Git residues.

## 2026-07-10 — Add invocation journal and verbose diagnostics (Codex)
**Did:** added metadata-only `invocations.jsonl` for successfully parsed CLI
commands and made `-v/--verbose` configure Python/Telethon debug output on
stderr. The journal records command, resolved account, exit/result metadata,
and duration, never message/search text, chat references, or raw parameters.
Updated CONTRACT, MAP, and ADR-0012.
**Decided:** journal write failures warn and preserve the command result;
mutation audit remains separately fail-closed (ADR-0011/0012).
**Next:** run the full regression suite and inspect the exact diff before any
commit.

## 2026-07-10 — Close minor Phase 3–5 review findings (Codex)
**Did:** added TDD regressions and fixed cancellation cleanup for atomic
exports, CSV formula injection in subscriber names, structured audit-write
failures, and media filename/checkpoint/progress throttling. A resume now
truncates bytes written after its last persisted checkpoint before continuing.
Updated CONTRACT, MAP, and ADR-0011. Verification: `uv run pytest -q` →
`183 passed, 8 skipped`; `git diff --check` passes.
**Decided:** audit persistence is fail-closed: an audit-path `OSError` is a
structured exit-2 policy block, so tgcli never makes an authorised unaudited
mutation (ADR-0011).
**Learned:** checkpoint throttling needs a matching resume rule; otherwise a
crash can leave a partial file longer than its persisted offset.
**Next:** commit these reviewed hardening fixes when requested.

## 2026-07-10 — Legacy Telegram MCP daemons decommissioned (Codex)
**Did:** unloaded the four `com.sereja.telegram-mcp-http*` LaunchAgents and
their four logrotate jobs from `gui/501`. Verified each service is absent from
launchd and no listener remains on ports 8799–8802. Preserved the matching
plist files, old-stack sessions, and unrelated `telegram-mirror-prime-set`.
**Decided:** tgcli is the sole live Telegram CLI route. Restoring a legacy MCP
daemon is a deliberate rollback operation, not a fallback agents may take.
**Next:** no roadmap work remains; maintain tgcli through normal scoped
changes and rerun the coverage gate on Telethon pin updates.

## 2026-07-10 — Roadmap completion doc pass (Codex)
**Did:** reconciled the master-plan status with completed acceptance evidence:
Phase 4 safe writes and Phase 6 migration/cutover are now marked complete.
**Decided:** all planned phases 0–7 are complete once Phase 7 PR #1 merges;
legacy daemon decommission remains outside the roadmap and requires a separate
explicit decision.
**Next:** merge PR #1, then treat future work as a new scoped feature rather
than an unfinished roadmap phase.

## 2026-07-10 — Phase 7 Telethon coverage closure (Codex)
**Did:** normalized `docs/FEATURES.md` to the 23 namespaces exposed by pinned
Telethon 1.44 and moved non-TL exclusions into prose. Added the executable
`scripts/check-coverage.py` gate and regressions for missing, unknown,
duplicate, malformed, and unexplained excluded classifications.
**Decided:** coverage is namespace-level: daily workflows are `wrapped`, raw
TL is `api`, and deliberately unsupported runtime models are `excluded` with
a reason. The checker is fail-closed and must run on every Telethon pin bump.
**Verified:** `.venv/bin/python scripts/check-coverage.py` reports
`coverage OK: 23 namespaces`; `.venv/bin/pytest -q` reports `177 passed,
8 skipped`.
**Next:** Phase 7 is complete; future Telethon upgrades must update the matrix
and pass the gate in the same commit.

## 2026-07-10 — Accept immediate tgcli cutover (Codex)
**Did:** removed the phase-6 parallel-window requirement from the master plan,
agent skill, and migration plan; updated global Claude routing to treat old MCP
daemons as legacy infrastructure rather than an ordinary fallback.
**Decided:** tgcli is the operational base after the completed local migration,
PATH cutover, and three-account read-only smoke. Legacy daemon decommission is
not implicit and still requires its own explicit authorization.
**Next:** push the verified local `main` to `origin/main`, then execute Phase 7
coverage closure when requested.

## 2026-07-10 — Retire unauthorized `pl` from Phase 6 migration (Codex)
**Did:** removed `pl` from the default import aliases, updated the CLI contract,
agent skill, phase plan, master plan, and ADR-0004, and added a regression that
proves default import ignores an existing old-stack `pl` directory.
**Decided:** `pl` is not a migration account until it is explicitly
reauthorized. Its old-stack source remains untouched; the tgcli config block
and copied state session are removed at the user's direction.
**Next:** push and merge the Phase 6 branch, then start the parallel-use window
with `main`, `recklessou`, and `teamsyncsage`.

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
**Live validation:** import returned `main: skipped_existing` and imported
`pl`, `recklessou`, and `teamsyncsage`. Read-only dialog smoke succeeded for
`main`, `recklessou`, and `teamsyncsage`; `pl` exits 3 because its old session
is not authorized. `scripts/install-link.sh` now resolves `tg` through
`~/.local/bin/tg`, and `tg --version` is `0.1.0`. Updated
`~/.claude/CLAUDE.md` so tgcli is first route and MCP is explicit fallback.
**Next:** reauthorize `pl` in the old stack and force-import it, or deliberately
retire that alias; begin the parallel-use window only after that decision.

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
