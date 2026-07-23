# DEVLOG

Append-only session log. Newest entry on top. Every agent session that
touches this repo adds one entry (AGENTS.md rule).

Sessions up to and including the 1.0.0 release live in
[DEVLOG-v1.md](DEVLOG-v1.md) and are closed — read them only when chasing
history, never to learn the current state.

Template:

```markdown
## YYYY-MM-DD — <short title> (<agent/model>)
**Did:** what actually changed (files, commands, results)
**Decided:** decisions made + link to ADR if architectural
**Learned:** surprises, gotchas, dead ends worth remembering
**Next:** the single most useful next step
```

## 2026-07-23 — Clone quote replies slices 0–1 (Composer)
**Did:** implemented plan slices 0 and 1 for ADR-0036. Slice 0:
`message_to_dict` now emits `quote_text` and, for cross-chat quotes, 
`reply_to` as `{"id", "peer"}` instead of a bare id that resolves against
the wrong chat; CONTRACT + live smoke shape updated. Slice 1:
`clone/replies.py` is a pure classifier
(`mapped-in-leg` / `mapped-cross-leg` / `foreign-peer` / `flatten`, stop on
invalid parent/quote/album/unrecognized header); `reply_from`/`reply_media`
no longer reject; transport consumes the classification and still builds
`InputReplyToMessage` only for mapped-in-leg (cross-leg/foreign flatten until
slice 2). Fixtures for source 2374/2378. Full gate: 680 passed, 8 skipped;
ruff + pyright + architecture + coverage green.
**Decided:** until `clone/quotes.py` lands, foreign-peer and mapped-cross-leg
flatten rather than wedge — progress with recorded loss beats a stuck cursor.
**Learned:** `topics.placement_only` is true for any forum_topic header
without a top id, including on non-forum destinations; reply_flattened must
gate that exception on `destination_kind == "forum"`.
**Next:** slice 2 — `clone/quotes.py` resolver (native quote / rendered
fallback by reachability).

## 2026-07-23 — Close the `mutual-chats --plain` test gap (Claude Opus 4.8)
**Did:** added `test_mutual_chats_plain_output_sanitizes_and_lists_chats` to
`tests/test_cli_mutual_chats.py` — asserts the frozen TSV column order
`id, type, username, display_name` plus tab/newline sanitization and the
empty-username cell. Then mutation-audited every projection in
`read_ops.execute`: swapped each of the 13 `Result(data, …_to_rows(data))`
lines for a mismatched projection and ran the full suite per mutant. All 13
are now killed; `mutual-chats` was the only survivor before the new test
(verified by re-running the mutant with the new test deselected: 674 passed).
`675 passed, 8 skipped`; ruff check/format clean.
**Decided:** nothing architectural — test-only change, no CONTRACT or MAP
impact.
**Learned:** the `mutual-chats` gap was worse than a crash. Swapping
`mutual_chats_to_rows` for `to_rows` does not raise `KeyError: 'peer'`,
because `mutual-chats` data carries both `peer` and `chats` — `--plain`
would have silently printed the resolved user instead of the common chats.
Line-targeted mutation of the projection table is a cheap coverage audit for
this repo (whole suite runs in ~4s, so 13 mutants cost under a minute).
**Next:** none for this thread; the same mutation script generalizes if the
`_SPECS`-style registry refactor of `read_ops` lands later.

## 2026-07-23 — CLI entry split + docs archive boundary (Claude Opus 4.8)
**Did:** split `cli.py` (909 lines, 7 functions) into four modules with one job
each — `parser.py` (375, grammar), `preflight.py` (168, pre-session validation
and gates), `dispatch.py` (213, network routing), `cli.py` (213, lifecycle);
turned `scripts/check-architecture.py` budgets from exact-equality baselines
into ceilings and extended its read-ownership check to all four modules;
repointed tests that patched `cli.session`/`cli.media_cmd` at the owning
modules; split `DEVLOG.md` at the 1.0.0 release into `DEVLOG-v1.md` (1,736
lines archived, 624 live); marked `docs/superpowers/` a closed archive in
place. ADR-0035. 674 passed / 8 skipped, ruff + pyright + architecture +
coverage gates green.
**Decided:** split the entry point by concern, not by command — ADR-0034 bars
the entry point from importing read command modules, so per-command grammar in
`commands/*.py` would have broken that seam. Kept `docs/superpowers/` where it
is: renaming would have rewritten 67 links, six of them inside ADRs that are
supposed to be immutable records.
**Learned:** the architecture gate compared line counts with `!=`, and a test
pinned that shrinking the file must fail too. That made `cli.py` frozen rather
than merely large — every edit, including one-liners, needed the budget
constant changed in the same commit, and it would have blocked this refactor
first. Also: the live documentation was never the problem. Of 18,295 doc lines,
only 1,691 are live contract; 66% was completed plans. The ratio that looked
like 2.7:1 against code is 0.25:1 once history is separated from contract.
**Next:** none pending. If `clone` is ever reopened, its 874-line command
module is the next hotspot with the same shape.

## 2026-07-23 — Shared read-operation architecture deepening (Codex)
**Did:** ran the scoped architecture-health survey over `cli.py`, clone
workflows/state, related history, tests, and ADRs; wrote the self-contained
before/after report to the OS temp directory; and selected the active
interactive/batch read seam over a mechanical clone split. Published spec
#23 and tracer tickets #24/#25. Added the closed typed `read_ops` module for
all 13 shared reads, moved batch and interactive dispatch behind it, and added
an exact-baseline/ownership architecture checker to CI. Permanent checker
regressions cover absolute/direct-symbol/relative imports, mixed
`media.manifest` dispatch, and unrecorded baseline shrinkage. Final full gate:
672 passed, 8 skipped; Ruff check/format clean; Pyright 0 errors; architecture
check passed; 23-namespace coverage passed.
**Decided:** ADR-0034. Interactive argparse/process/output and batch
JSONL/sequencing/error envelopes stay separate adapters; coercion and command
selection live in one deep typed module. CONTRACT is unchanged because flags,
JSON, TSV, exits, safety, and Telegram requests are unchanged.
**Learned:** the strongest hotspot was not raw clone line count but duplicated
knowledge across two currently changing adapters; the 2026-07-23 ISO fix was
concrete evidence. Independent Spec + Standards review found ratchet bypasses
through direct/relative imports and the mixed media module; four red
regressions plus the relative-import regression now fail those paths closed.
**Next:** publish the implementation PR, wait for green CI and mergeability,
then merge without running Telegram mutations.

## 2026-07-23 — Engineering skill flow setup (Codex)
**Did:** configured the repository for the Matt Pocock engineering flows:
GitHub Issues as the tracker, canonical triage-label routing, and single-context
domain docs that preserve `docs/decisions/` as the only ADR directory. Added
`docs/agents/`, ADR-0033, AGENTS/CLAUDE routing, and MAP/index entries; created the
four missing GitHub labels (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`; `wontfix` already existed). Full gate: 664 passed, 8
skipped; Ruff check/format, Pyright, and 23-namespace coverage passed.
**Decided:** `CONTEXT.md` remains lazy and should appear only when domain
modeling produces durable vocabulary. Pull requests are implementation
artifacts, not an incoming triage surface.
**Learned:** the generic flow defaults use `docs/adr/`, which would duplicate
tgcli's established `docs/decisions/`; repository-local routing must override
that default explicitly.
**Next:** hand off the line-budget evidence to a fresh architecture-health
session and run the scoped deepening survey.

## 2026-07-23 — Retrospective PR fixes and review correction (Codex)
**Did:** audited merged PRs 18, 20, and 21 from the last whole-branch review
point; confirmed PR 19 never merged. Added regression coverage and minimal
fixes for batch blank-line caps and ISO dates, combined bulk-media filters,
typed partial-failure exits, output ownership, auth-revocation propagation,
broadcast export flood waits, unoccupied phone resolution, and atomic
cross-process resolve-phone cooldown reservations. Updated CONTRACT, MAP, and
SKILL. Final gate: 664 passed, 8 skipped; Ruff check/format, Pyright, and the
23-namespace coverage check all passed. Independent final Spec review found no
remaining actionable defect.
**Decided:** withdraw the earlier `mutual-chats` type finding: Telethon's
`GetCommonChatsRequest.resolve()` converts the high-level entity through
`get_input_user`, so no production change is required. Historical PR 18
sequencing/scope issues are recorded as process findings, not rewritten.
**Learned:** whole-diff adversarial review found contract failures that focused
happy-path tests and green aggregate CI missed, especially JSON-to-domain
coercion, concurrent cooldown reservation, and exception mapping inside a
partial-success loop.
**Next:** review the published fix branch and merge it when ready.

## 2026-07-23 — Independent implementation and review gates (Codex)
**Did:** strengthened `AGENTS.md` with public-seam TDD, exact Telethon request
type checks, coherent PR scope, independent whole-diff Spec + Standards review,
adversarial CLI coverage, safe live-smoke boundaries, and the complete uv
verification gate. Replaced the duplicated Cursor rule set with a thin adapter
and explicit implementation/handoff checklist; removed its contradictory
`commit (if asked)` instruction.
**Decided:** green focused tests and CI are necessary but not sufficient;
feature authors do not provide the only final approval of their own work.
**Learned:** permissive fakes and copied agent rules can both hide drift:
external type mismatches in code and workflow contradictions in instructions.
**Next:** finish the active PR #21 regression fixes, then apply the independent
whole-diff gate before merge.

## 2026-07-23 — Post-merge review of PRs 20 and 21 (Codex)
**Did:** reviewed PR #20 (`f7542b6`) and PR #21 (`6096519`) against their
contracts, ADRs, and repository standards. PR #20 was clean; focused API tests
reported 81 passed. PR #21's full gate reported 652 passed, 8 skipped, with
Ruff, format, Pyright, and the 23-namespace coverage gate clean. The review
found four functional contract defects in PR #21: wrong `TypeInputUser`
construction for `mutual-chats`, rejected/ignored combined bulk-media filters,
blank lines counted against the batch op cap, and unparsed batch ISO date
filters. A focused repro confirmed the date-filter `datetime`/`str` TypeError.
**Decided:** treat the findings as follow-up maintenance fixes; this review
changes no production code and makes no new architectural decision.
**Learned:** mocked request handlers and aggregate green gates did not exercise
Telethon's exact request-field type or batch JSON-to-domain coercion.
**Next:** fix PR #21 findings test-first, starting with `mutual-chats` and batch
date parsing.

## 2026-07-23 — Slice 5: read-only tg batch (Composer)
**Did:** ADR-0032 slice 5 — `tg batch` JSONL runner (cap 100, RO allowlist,
any failure → nonzero exit, `--fail-fast`, no doctor). New
`commands/batch.py`; CONTRACT/MAP/SKILL/PROPOSALS updated.
**Decided:** stdin parsed before session open so allowlist/cap fail closed.
**Learned:** none.
**Next:** owner live smoke of archive/mute and a small batch against main.

## 2026-07-23 — Slice 4: bulk media download (Composer)
**Did:** ADR-0032 slice 4 — `media download --message-ids` and filter
`--type/--since/--limit` (cap 100). PartialFailure emits JSON with
`failed[]` and nonzero exit. CONTRACT/SKILL/PROPOSALS/MAP updated.
**Decided:** PartialFailure exception carries result document for stdout.
**Learned:** none.
**Next:** Slice 5 — read-only `tg batch`.

## 2026-07-23 — Slice 3: incremental export messages (Composer)
**Did:** ADR-0032 slice 3 — `export messages --after-id/--append/--resume`.
Append requires a cursor; resume parses last JSONL `id` (fail closed).
CONTRACT/PROPOSALS/DEVLOG updated.
**Decided:** none beyond ADR-0032 grilling.
**Learned:** none.
**Next:** Slice 4 — bulk media download.

## 2026-07-23 — Slice 2: dialog archive and mute (Composer)
**Did:** ADR-0032 slice 2 — `dialog archive|unarchive|mute|unmute`. Mute
requires `--until` or `--forever` (exit 2 otherwise). Same pin-style
gate/audit, no preview. CONTRACT/SKILL/PROPOSALS/MAP updated.
**Decided:** forever = `mute_until = 2**31-1`; unmute = `0`.
**Learned:** none.
**Next:** Slice 3 — incremental export messages.

## 2026-07-23 — Slice 1: tg mutual-chats (Composer)
**Did:** ADR-0032 slice 1 — `tg mutual-chats <user>` over
`messages.GetCommonChatsRequest` (limit 100). Returns `{peer, chats, count}`;
empty chats ok; non-user peer exit 2; missing user exit 4. CONTRACT/SKILL/
PROPOSALS/MAP updated. Tests in `tests/test_cli_mutual_chats.py`.
**Decided:** bots allowed as the mutual peer (Telegram supports getCommonChats
on bots that share groups); groups/channels as the ref stay blocked.
**Learned:** none.
**Next:** Slice 2 — dialog archive/mute.

## 2026-07-23 — ADR-0032 data plumbing scope + plan (Composer)
**Did:** owner-approved PROPOSALS package (mutual-chats, dialog
archive/mute, incremental export, bulk media, RO `tg batch`) grilled and
accepted as ADR-0032; wrote the ADR, index row, MAP decisions line, and
`docs/superpowers/plans/2026-07-23-data-plumbing.md`. Out of scope remains
export bundle / MSG-001 / FEED-001 / ACCOUNTS-001 / moderation verticals.
**Decided:** ADR-0032. One PR with five slice commits; mute requires
`--until` or `--forever`; batch and bulk media hard-capped at 100; batch
exit nonzero on any failed op; doctor not in batch allowlist.
**Learned:** grilling closed the agent footguns before code.
**Next:** Slice 1 — `tg mutual-chats`.

## 2026-07-23 — Cursor onboarding + pyright reply_to_ephemeral (Composer)
**Did:** Cursor first-session track C+A from the orientation plan. Added
always-apply rule `.cursor/rules/tgcli-maintenance.mdc` (maintenance mode,
TDD, docs discipline, how-to-task examples). Mapped it in `docs/MAP.md`.
Fixed frozen pyright baseline in `clone/replies.py` via
`getattr(header, "reply_to_ephemeral", False)` plus a unit test that
rejects ephemeral reply shapes. Verified: `tg doctor --json` ok (3
accounts); `.venv/bin/pytest -q` → 627 passed, 8 skipped; ruff clean;
pyright 0 errors.
**Decided:** gated product work (MSG-001 / FEED-001 / PROPOSALS) stays
owner-triggered after onboarding; no ADR for the getattr hardening.
**Learned:** system `pytest` outside `.venv` fails collection; always use
`.venv/bin/pytest`. Sandbox blocks session locks / invocation journal —
doctor needs unrestricted FS for live auth checks.
**Next:** owner picks one gated item (`tg batch`, albums, `tg changes`,
or `accounts login`) or a live Telegram task.

## 2026-07-23 — Fix tg api bool/scalar RPC serialization (Composer)
**Did:** bug fix for live finding: `tg api account.updateStatus --write`
reached Telegram then crashed on `bool.to_dict()`. Added failing unit test
and `_serialize_rpc_result` so TLObjects still use `to_dict()` while bare
Bool/int/null pass through `_sanitize_result`. CONTRACT §6 notes scalar
`result`. Suite: `81` api-related tests green; ruff + pyright clean.
**Decided:** serialize scalars as JSON values in `result` (additive contract
clarification), not wrap them in a fake TL dict.
**Learned:** many MTProto writes return Bool; the previous envelope assumed
every RPC result was a TLObject.
**Next:** open PR for `claude/fix-api-bool-result` (live recheck on main:
`{"method":"account.updateStatus","result":false}` exit 0).

## 2026-07-23 — Remainder live checks without clone (Composer)
**Did:** finished non-clone live remainder on `main`. PASS: accounts list,
media manifest (+ `--type photo`), info `--full`, search `--all`, message
`--context`, `stories.getPeerStories`, export subscribers `--limit 201` →
exit 2 (ADR-0031), custom_emoji id as decimal string + HTML send with
`<tg-emoji>`, send `--file`+caption+`--silent` then delete, api hard-deny
`auth.logOut` → exit 2, resolvePhone invalid + local cooldown → exit 5.
**Decided:** none (verification).
**Learned:** `tg api account.updateStatus --write` reaches Telegram (Bool
ok) but crashes: `AttributeError: 'bool' object has no attribute 'to_dict'`
in `commands/api.py` `call()` — bool/int RPC results are not handled.
**Next:** owner call — fix bool/scalar sanitize in `tg api` (bug), or stop.

## 2026-07-23 — Extended live checks on main beyond bench (Composer)
**Did:** owner-requested live pass for commands outside smoke/bench on
`main`, mostly via Saved Messages. PASS: doctor, contacts list/search,
resolve `@CrwDdy`, thread (parent+reply then cleanup delete), HTML
send/edit preview→commit, forward me→me, mark-unread/read, dialog
pin/unpin, delete preview→commit. Left visible proof in Saved Messages
(HTML-edited message + its forward). Initial script falsely flagged
`resolve me` (exit 0; shape is `{peer:{id,…}}`, not top-level `id`) —
recheck confirmed PASS.
**Decided:** none (verification only).
**Learned:** `resolve me` returns a `peer` envelope; do not assert bare `id`.
**Next:** none unless owner wants phone-resolve or custom-emoji send.

## 2026-07-23 — Live smoke + bench on main (Composer)
**Did:** ran owner-requested live verification on account `main` (no clone).
`TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` → `8 passed` (~8.5s).
`.venv/bin/python scripts/bench.py --account main` → `13/13 PASS`, 0 skip,
0 fail, ~16.4s total. Bench wrote one Saved Messages ping and exercised
read/search/message/info/count/api/send/media-download/export paths.
**Decided:** none (verification only).
**Learned:** no FloodWait on this run; export-messages and export-subscribers
both PASS (subscribers default `@mir_ivanova`).
**Next:** optional deeper checks (doctor/contacts/edit-delete-forward preview)
if needed; clone stays owner-gated separately.

## 2026-07-23 — PR #18 review fixes: export limit, emoji id string, resolvePhone cooldown, ADR-0031 (Composer)
**Did:** addressed Bugbot/Standards/Spec/thermo-nuclear findings on
`claude/agent-quick-wins`. (1) Broadcast `export subscribers --limit > 200`
now exits 2 instead of full-crawl + arbitrary slice; unlimited omit-`--limit`
keeps prefix-union (ADR-0031). (2) `custom_emoji[].id` emitted as decimal
string. (3) Shared ~3s `resolve_phone` cooldown for `tg resolve +…` and
`tg api contacts.resolvePhone`. Docs: CONTRACT, MAP, SKILL, ADR-0010 note,
ADR-0031 + index.
**Decided:** reject finite broadcast limits above the page size rather than
invent a “most recent N past 200” API Telegram does not offer cheaply.
**Learned:** review consensus across four agents was stronger on export
semantics and JS id precision than on the argparse exit-1 vs plan exit-2 nit.
**Next:** merge PR #18 after gates.

## 2026-07-23 — ADR-0029 slice 3: media manifest + thread (Composer)
**Did:** executed discovery-inbox Tasks 6–7. `tg media manifest CHAT` dry-run
inventory (`--type`/`--since`/`--limit`, no download) in `media.py`. New
`commands/thread.py` + `tg thread CHAT MESSAGE_ID` with ancestor walk
(depth default 20, hard cap 100, cycle-safe) and opt-in `--replies` via
`get_messages(reply_to=…)` when `message.replies` exposes a cheap thread.
FakeClient `get_messages` gains `reply_to` + `replies=` map. CONTRACT, MAP,
SKILL, PROPOSALS updated; ADR-0029 plan fully shipped.
**Decided:** argparse `--type` invalid choice stays exit 1 (repo convention),
not PolicyError exit 2; newest-first `--since` stops at the first older
message rather than scanning past it.
**Learned:** Telethon `get_messages(reply_to=)` is enough for forum/comment
threads without a raw `GetRepliesRequest` in the wrapper.
**Next:** optional PR for `claude/agent-quick-wins`; backlog remains
mutual-chats / bulk media / incremental export / batch / dialog archive-mute.

## 2026-07-23 — ADR-0029 slice 2: mark-unread + dialog pin/unpin (Composer)
**Did:** executed discovery-inbox Tasks 4–5. `tg mark-unread CHAT` via
`MarkDialogUnreadRequest(unread=True)` in `mutate.py`, with the same
`--readonly` / env gating and fail-closed audit as `mark-read`. New
`commands/dialog.py` + `tg dialog pin|unpin CHAT` via
`ToggleDialogPinRequest`. FakeClient accepts both Bool TL requests. Tests in
`test_cli_mutate.py` / `test_cli_dialog.py`. CONTRACT, MAP, SKILL, PROPOSALS
updated.
**Decided:** keep `mark-unread` top-level (mirrors shipped `mark-read`) and
pin/unpin under the `dialog` namespace, per ADR-0029 — not a unified
`dialog mark-unread`.
**Learned:** both TL methods take `InputDialogPeer`, not a bare InputPeer.
**Next:** ADR-0029 slice 3 — `media manifest` then `tg thread`.

## 2026-07-23 — Close WIP: formatting/export/stories docs + export test (Composer)
**Did:** finished the uncommitted 2026-07-22 WIP on `claude/agent-quick-wins`.
Added a reproducing unit test that broadcast `export subscribers` unions
prefix searches past the page cap (FakeClient hooks for
`GetFullChannel`/`GetParticipants`). Wrote ADR-0030 (outgoing `--format` +
`custom_emoji`); expanded ADR-0010 to 40 methods (`stories.*`); updated
MAP, CONTRACT (broadcast subscriber walk), ISSUES MSG-001, FEATURES,
ADR-0028 deferred note. Formatted `export.py` / `test_cli_send.py`.
**Decided:** formatting lands as ADR-0030 (MSG-001 partial), not a silent
CONTRACT-only change; stories stay an ADR-0010 allowlist bump.
**Learned:** existing subscriber tests still hit `iter_participants` because
the fake channel entity has no `broadcast=True` — the aggressive path needs
an explicit broadcast fixture.
**Next:** commit the WIP (one or more commits), then push / open PR for
`claude/agent-quick-wins`; resume ADR-0029 slices 2–3 when ready.

## 2026-07-22 — Full broadcast-subscriber export + stories read allowlist (Claude Opus 4.8)
**Did:** (1) `export subscribers` now returns every member of a broadcast
channel instead of the 200 Telegram caps a single `getParticipants` at. New
`_iter_all_channel_members` (`src/tgcli/commands/export.py`) unions saturating
prefix searches over a latin+digit+cyrillic alphabet, deepening any prefix that
fills a full 200-page, deduping by id, stopping once the reported member total
is reached. Aggressive path triggers only for broadcast channels when no small
`--limit` is set (`limit is None or limit > 200`); megagroups and explicit small
limits keep the plain single-pass `iter_participants`. Verified on
@mir_ivanova: 200 → 280/281 members. (2) Added four read-only `stories.*`
methods (`getPeerStories`, `getStoryViewsList`, `getStoriesArchive`,
`getStoriesByID`) to `READ_METHOD_ALLOWLIST` (`src/tgcli/commands/api.py`) +
the reviewed-list test, so story-viewer analytics work via `tg api`.
**Decided:** raw `GetParticipantsRequest` + **sequential** issue, not
`iter_participants` and not concurrency. `iter_participants` fires a second
request per search purely to compute a `count` we discard (2× the calls);
issuing prefix queries concurrently reliably trips server flood-wait (measured:
8 concurrent = 5.6s dominated by one flood-stalled call, vs 8 sequential =
3.2s @ ~0.4s each). Net: naive full enumeration 3:19 → tuned 1:07.
**Learned:** for **broadcast** channels the 200 cap is hard for both
`ChannelParticipantsRecent` and `ChannelParticipantsSearch('')` — `offset>200`
returns zero rows and `.count` itself reads 200, so pagination can't see past
it. Prefix-substring search is the only escape and its wall-time floor is set
by Telegram's getParticipants flood-limit, not local work. Emoji/CJK-only
display names with no searchable char can leave a member unreachable (got
280 of a reported 281), so early-break on total may not fire — acceptable.
**Next:** consider a `--fast`/`--complete` toggle if the ~1min full sweep is
too slow for interactive use on large channels.

## 2026-07-22 — Fix edit-commit peer + add `edit --format` (Claude Opus 4.8)
**Did:** two changes to the edit surface. (1) Bugfix: `commit_edit` passed the
raw stored `chat` string straight to `tg.edit_message`, so editing a private
channel by numeric `-100…` id died with `Cannot find any entity`. Wrapped it in
`chatref.parse()` to match `commit_forward`/`send.commit`
(`src/tgcli/commands/mutate.py:50`). (2) Feature: new `src/tgcli/formatting.py`
(`render(text, fmt)` → `(clean_text, entities|None)`), a `--format
{plain,md,html}` flag on `tg edit` (default `plain`), threaded through the
preview payload and re-rendered at commit into explicit `formatting_entities`.
`html` reuses Telethon's HTML parser (bold/italic/underline/strike, blockquote +
`expandable`, code/pre, links, `tg-emoji` custom emoji) and subclasses it to add
`<tg-spoiler>` / `<span class="tg-spoiler">`, which Telethon 1.44 does not emit.
Added `tests/test_formatting.py` (10) + 3 CLI tests; updated the edit fakes to
accept the new kwargs. 594 passed. Updated CONTRACT (edit preview row gains
`format`; new `--format` paragraph).
**Decided:** `edit` defaults to `plain` = parse disabled (verbatim TEXT, no
entities), deliberately *not* inheriting the client Markdown default that
`send` documents — surgical edits should be literal unless formatting is asked
for. Formatting is stored as the raw markup + format name in the preview and
re-rendered at commit, keeping the preview payload JSON-safe. Extends ADR-0028.
**Learned:** Telethon 1.44's HTML parser already handles expandable blockquote
and `tg-emoji`, but silently drops spoiler tags (no entity, text kept) — easy to
miss without checking entity types. Telegram entity offsets are UTF-16 code
units, so surrogate-pair emoji (💸) must shift following offsets by 2; the
subclass inherits Telethon's `add_surrogate`/`strip_text` machinery to get this
right (covered by a dedicated test).
**Next:** consider mirroring `--format` onto `send` for parity (send still uses
the client Markdown default), and surfacing entities in `tg message` read output
so formatted posts can be verified without a raw TL call.

## 2026-07-22 — `send --format` parity + custom-emoji harvest (Claude Opus 4.8)
**Did:** (1) mirrored `--format {plain,md,html}` onto `send` (default `md`, so
existing behavior + CONTRACT hold), routing prepare/commit through
`formatting.render` instead of the client's implicit `_parse_message_text`;
`format` is stored in the preview payload and appended to the send preview row.
(2) Added custom-emoji harvesting: `message_to_dict` now emits `custom_emoji`, a
list of `{id, emoji, offset, length}` extracted from `MessageEntityCustomEmoji`,
so ids can be pulled from any readable post (e.g. тень.exe) and reused as
`<tg-emoji emoji-id="ID">` in `--format html`. Glyphs are sliced with Telethon
surrogate helpers (UTF-16). Updated 5 exact-match message fixtures + 3 CONTRACT
JSON examples, added 4 send tests + 1 read test. 598 passed.
**Decided:** `send` keeps `md` as default (composition convenience, documented),
while `edit` stays `plain` (surgical, literal) — deliberate asymmetry. Custom
emoji surfaced on the universal message shape rather than a bespoke command, so
`read`/`search`/`message`/`export` all expose ids uniformly (additive field).
**Learned:** entity offsets index into `Message.message` (raw), not the
`.text` property (which re-renders markup) — slicing `.text` would drift when
markup is present; fakes only carry `.text`, so the helper prefers `.message`
and falls back. Telethon 1.44 `MessageEntityCustomEmoji.document_id` is the same
id `<tg-emoji emoji-id>` consumes, so harvest→reuse round-trips without a map.
**Next:** optionally let `--format html` accept a shorthand for pasting a raw
unicode+id pair, and add an `entities` passthrough for the long tail (underline
mixes, nested quotes) if a real post needs it.

## 2026-07-21 — Slice 1: resolve + contacts commands (Claude Sonnet 5)
**Did:** executed ADR-0029 slice 1 (`fae503f`..`7102c6b`): allowlisted
`contacts.resolvePhone` as a read method (ADR-0010), added `tg resolve REF`
(resolves `@username`, `t.me` link, numeric id, or `+phone` to a single
`{peer:{id,type,username,display_name,is_contact,is_bot}}`), and added
`tg contacts list` / `tg contacts search QUERY [--global]` in the new
`src/tgcli/commands/identity.py`, reusing the `peer_to_dict` mapping. Local
`contacts search` filters the address book in Python (`scope: "local"`);
`--global` calls `contacts.search` capped at 50 results (`scope: "global"`).
Closed out the slice with three doc edits: this entry, an ADR-0010
reconciliation (`contacts.resolvePhone` moved from "reviewed and rejected"
into the allowlist, 35 → 36 methods), and a CONTRACT.md note on the
`--global` 50-result cap.
**Decided:** phone lookup calls `contacts.resolvePhone` only, never
`contacts.importContacts` — the caller must already hold the phone number,
and Telegram returns not-found (exit 4) when the target's privacy settings
block the lookup, so the allowlist entry cannot be used to enumerate numbers.
**Learned:** ADR-0010's own Consequences require an ADR-0010 update whenever
the read allowlist changes; the resolvePhone allowlisting commit shipped
without that follow-up, leaving the ADR self-contradictory (method both
allowlisted in code and listed under "rejected" in the doc) until this
closeout.
**Next:** all four gates green (581 passed, 8 skipped); proceed to slice 2
per the scoped plan.

## 2026-07-21 — ADR-0029: discovery & inbox scope + plan (Claude Opus 4.8)
**Did:** synced stale local main to origin (was 34 behind), vetted the owner's
feature wishlist against the real post-PR-#17 surface, and wrote
docs/PROPOSALS.md (backlog). Then grilled the top-5 quick wins and captured the
decisions as **ADR-0029** + scoped plan
docs/superpowers/plans/2026-07-21-discovery-inbox.md (3 slices, 7 tasks).
Updated decisions/README, MAP.md, ISSUES.md link, PROPOSALS graduation note.
No production code yet — awaiting owner go per ADR-0026.
**Decided (grill outcomes):** one ADR for all 5; inbox mutations
(mark-unread, dialog pin/unpin) run direct like mark-read, no preview;
`resolve` supports +phone via `contacts.resolvePhone` ONLY (added to read
allowlist, never importContacts); `contacts search` local by default, `--global`
opts into contacts.search; `thread` = ancestors always (depth 20, cap 100) +
replies only via getReplies under `--replies`; `media manifest` = dry-run with
--type/--since/--limit; `mark-unread` top-level (mirrors mark-read), pin/unpin
under `tg dialog`.
**Learned:** always `git fetch` + check origin/main before a coverage audit —
the first pass on a stale tree wrongly flagged shipped commands as missing.
resolvePhone is the only new safety-surface change, which is what makes an ADR
required rather than optional.
**Next:** on owner go, execute slice 1 (allowlist resolvePhone → resolve →
contacts) TDD, one task per commit.

## 2026-07-21 — Export @mir_ivanova subscribers to Google Sheets (Codex)
**Did:** performed a live read-only participant export for `@mir_ivanova`,
including Telegram join timestamps, then built and visually verified a
three-column workbook and imported it through `gog` as a native Google Sheet.
Verified 284 exported API-visible users against Telegram's visible counter of
286, 285 populated rows including the header, oldest-to-newest date order, and
the first/last ranges after Google conversion. No production code changed.
**Decided:** report the result as Telegram API-visible maximum rather than exact
counter equality because both the normal and exhaustive search-slice passes
left the same two-user counter gap. Used the channel creation timestamp for the
creator's otherwise-null join date.
**Learned:** `ChannelParticipantsRecent` stopped at 200 for this broadcast
channel; alphabetic search slices recovered 84 more users, while extended
Unicode slices recovered none beyond that. Telegram management and future
subscriber-export work must use `tgcli`; no legacy Telegram project is an
active fallback or dependency.
**Next:** use the Google Sheet as the handoff artifact; if join-date search
slices become recurring work, scope that capability directly in `tgcli` under
the maintenance-mode gate.

## 2026-07-21 — PROPOSALS: vet owner wishlist against real surface (Claude Opus 4.8)
**Did:** local main was 34 commits behind origin (pre-PR-#17); first pass
analysed a stale tree and wrongly flagged shipped commands as missing.
Fast-forwarded to origin/main, re-checked the real surface, and wrote
docs/PROPOSALS.md — only genuinely-new, un-vetted ideas. Cross-linked from
ISSUES.md, added the MAP.md row. No code changed.
**Decided:** nothing approved (ADR-0026). PROPOSALS holds contacts/`resolve`,
`thread`, read-only `batch`, incremental export + bulk media/`manifest`,
`dialog` state, and the deferred verticals (community/moderation, stats,
security). Messaging tail and change feed are NOT here — already tracked as
MSG-001 / FEED-001 (ADR-0028); wanting one is a re-entry, not a new proposal.
**Learned:** verify the checkout is current before auditing coverage — a
stale local main produced a confident but wrong "these commands don't exist".
`tg doctor` already covers the whoami need; `read --after-id/--since/--until`
already provides the interim change-feed polling path.
**Next:** await owner pick; identity layer (`resolve`, `contacts`) is the
cheapest high-value new work if they want to start.

## 2026-07-18 — File-send TOCTOU snapshot closure (Codex)
**Did:** closed the final file-send TOCTOU gap. Preview now derives byte count
and SHA-256 from one open stream. Commit copies one open of the approved source
into a unique temporary snapshot while computing the same fingerprint,
compares both values to the preview, uploads only that snapshot, and removes it
after success or upload/request/confirmation failure. Original-path MIME and
filename metadata are preserved. Added an adversarial upload hook that replaces
the original after validation and cleanup coverage for all outcomes; amended
the canonical ADR-0028 plan Tasks 7/8 and CONTRACT. Final local gates:
`uv run pytest -q` — `570 passed, 8 skipped in 2.70s`; `uv run ruff check .` —
passed; `uv run ruff format --check .` — `86 files already formatted`; `uv run
pyright` — `0 errors, 0 warnings, 0 informations`; `uv run python
scripts/check-coverage.py` — `coverage OK: 23 namespaces`.
**Decided:** the temporary snapshot is implementation hardening inside
ADR-0028's approved send contract, not a new state model or architectural
surface; it needs no dependency, MAP change, or new ADR.
**Learned:** validating a path and then handing the same path to an uploader is
still unsafe because the uploader reopens it. Hashing the exact bytes copied to
an isolated snapshot makes the proof and the uploaded object identical.
**Next:** run the final whole-branch rereview before pushing.

## 2026-07-18 — Agent correspondence final-review fixes (Codex)
**Did:** applied all five whole-branch review fixes without touching clone:
file-send previews now expose and store SHA-256 and commits revalidate the
absolute path, size, and digest before upload; repeated edits treat
`MessageNotModifiedError` as convergence; raw text and media sends preserve
Telethon's default parse mode and entities; dialog filtering classifies
megagroups as groups; and the unread recipe now drains a fixed checkpoint
window before oldest-first processing. Added changed-size, same-size
replacement, text/caption Markdown, ambiguous edit retry, and megagroup versus
broadcast regressions. Final local gates: `uv run pytest -q` — `566 passed, 8
skipped in 2.56s`; `uv run ruff check .` — passed; `uv run ruff format --check .` — `86
files already formatted`; `uv run pyright` — `0 errors, 0 warnings, 0
informations`; `uv run python scripts/check-coverage.py` — `coverage OK: 23
namespaces`.
**Decided:** no new ADR or dependency is needed: these are narrow correctness
fixes inside ADR-0028's approved send, edit, discovery, and agent-recipe
surface. `docs/MAP.md` remains accurate because no module moved or changed
ownership.
**Learned:** switching an idempotent send to raw TL requests also bypasses the
high-level client's default text parsing unless `_parse_message_text(..., ())`
is applied explicitly; file size alone cannot bind a preview to same-size
replacement contents.
**Next:** run the requested whole-branch rereview before pushing the completed
commit.

## 2026-07-18 — Agent correspondence Task 15 docs closure (Codex)
**Did:** updated `SKILL.md` for the complete ADR-0028 correspondence surface:
`edit`, `delete`, `forward`, `mark-read`, and `doctor`, plus recipes for
walking `read --before-id` history, checking unread dialogs with
`read --after-id`, and retrying the same send commit after a network failure.
Re-read `docs/MAP.md` against `src/tgcli/`: `confirm.py`, `commands/mutate.py`,
and `commands/doctor.py` already have accurate rows, so the map needed no
churn. Final local gates: `.venv/bin/pytest -q` — `560 passed, 8 skipped`;
`.venv/bin/ruff check .` — passed; `.venv/bin/ruff format --check .` —
`86 files already formatted`; `uv run pyright` — `0 errors, 0 warnings`.
**Decided:** this task closes documentation and local quality gates only;
the v1.1 label is the ADR-0028 surface name, not a release-version bump.
No new ADR is needed because ADR-0028 already authorizes the commands and
their safety behavior. ADR-0028's rejected and deferred boundaries remain
unchanged, and clone is untouched.
**Learned:** send and forward retry safety depends on reusing the original
preview ID, whose stored `random_id` allows Telegram confirmation without a
duplicate mutation; a new preview is not an equivalent retry.
**Next:** after merge, perform the owner-gated live visual acceptance:
`tg doctor --json`, `tg dialogs --unread-only --json`, and
`tg search --all` against a known string. This live smoke was not run in this
documentation-only task.

## 2026-07-18 — Task 14 doctor review fixes (Codex)
**Did:** made the doctor online probe convert every ordinary exception into
`checks.error` and exit-0 report data; a missing `.session` now reports
`lock_free: false` without creating a lock file. Added regressions for both
cases and documented the short-lived local lock/writability probes.
**Decided:** `doctor` still acquires a lock only for an existing session, and
its local probe cleanup is best-effort; no Telegram mutation is permitted.
**Learned:** an absent session and an available lock are different health
facts, so `lock_free` must not be inferred by probing a nonexistent session.
**Next:** review the corrective commit, then run the owner-gated live smoke.

## 2026-07-18 — Agent correspondence Task 14 doctor health report (Codex)
**Did:** added `tg doctor`, which inspects every configured account (or one
explicit alias) for session presence, lock availability, writable local state,
and Telegram authorization; added JSON/TSV contract documentation and three
CLI tests covering all accounts, account filtering, and a reported auth/config
failure. Focused test result: `3 passed`.
**Decided:** online `ConfigError` is health data, not a CLI error: `doctor`
returns exit 0 after a completed check and callers inspect `ok`. No Telegram
mutation occurs; the state-writability probe is local and temporary.
**Learned:** doctor must invoke `tgcli.session.client` directly, so tests patch
that module path instead of only a `cli` import alias.
**Next:** run the full quality gates and perform the owner-gated live health
smoke after merge.

## 2026-07-18 — Agent correspondence Slice 2 mutation surface complete (Codex)
**Did:** completed Tasks 5–10 of the ADR-0028 plan: `send` now previews and
commits text/files with reply, topic, silent, and stored `random_id` metadata;
retryable previews move `.json` → `.pending` → `.used`; `edit`, `delete`, and
`forward` use preview→commit; and `mark-read` is a gated, audited direct
mutation. Forward commits use the preview's stored source, destination, and
`random_id`, then fail-close unless Telegram confirms the exact message id.
Added the JSON/TSV contracts and TDD coverage for forward confirmation and
mark-read readonly behaviour.
**Decided:** Slice 2 stays within ADR-0028's existing safety model. Forward
does not add reply/topic flags: it uses Telegram's native forward semantics
from the stored source peer to the stored destination peer, so it creates no
reply header. `mark-read` remains preview-free because it is content-free and
idempotent, while still requiring readonly/no-send gates and a pre-dispatch
audit record.
**Learned:** the frozen clone pyright baseline still has the unrelated
`src/tgcli/clone/replies.py:28` missing-stub error for
`MessageReplyHeader.reply_to_ephemeral`; Slice 2 did not touch clone code.
**Next:** merge Slice 2 and perform the owner-gated live visual smoke: file
send with caption and reply, edit, delete, and forward in a private test chat.

## 2026-07-18 — Agent correspondence Slice 1 read surface complete (Codex)
**Did:** completed Tasks 1–4 of the ADR-0028 agent-correspondence plan: added
the shared agent-facing message fields; `read` ID/date/topic filters and page
metadata; `message --context`; and `search --from` / `--since`. Documented
the additive response and filter contracts. The full local test suite reported
`492 passed, 8 skipped`; ruff check and format gates passed.
**Decided:** all Slice 1 changes remain additive to existing JSON and TSV
contracts. Date lower bounds preserve Telethon's newest-first iteration and
stop at the first older message; sender filtering is delegated through
Telethon's `from_user` parameter. No ADR was needed because ADR-0028 already
authorizes this scoped, contract-additive work.
**Learned:** the repository's current pyright baseline has one unrelated error
in frozen clone code, `src/tgcli/clone/replies.py:28`, for the absent
`MessageReplyHeader.reply_to_ephemeral` stub attribute; it was not changed.
**Next:** start Slice 2 only from its first TDD task: preview commit state in
`safety.py`, with its new failure/retry semantics tested before implementation.


## 2026-07-18 — ADR-0028: v1.1 agent correspondence scope + plan (Claude Fable 5)
**Did:** owner-commissioned product review of v1.0 walked through a
structured grilling session; wrote ADR-0028 (scope: richer message JSON,
id/date pagination, full mutation set under preview→commit with
random_id commits, discovery flags, `tg doctor`), the scoped plan
`docs/superpowers/plans/2026-07-18-agent-correspondence.md` (15 tasks,
3 slices), ISSUES additions MSG-001 and FEED-001, ADR index row. Docs
only — no code yet.
**Decided:** ADR-0028. Rejected: `tg spec`, `tg can`, `tg inbox`, keyed
idempotency journal, opaque cursors. Deferred with triggers: MSG-001,
FEED-001; ACCOUNTS-001 keeps its trigger. Clone stays untouched —
`random_id` confirmation is deliberately duplicated into a new
`tgcli/confirm.py` instead of refactoring the frozen clone.
**Learned:** two review claims were already implemented (structured
FLOOD_WAIT with `retry_after` in CONTRACT §2; t.me links accepted as
chat refs everywhere) — verify review claims against code before
planning around them. `consume_preview`'s burn-on-consume design is what
makes network-failure retries unsafe today; the `.pending` state fixes
that without a journal subsystem.
**Next:** owner reviews the plan; then execute slice 1 (tasks 1–4) on a
`claude/agent-correspondence-s1` branch.

## 2026-07-17 — ISSUES: pre-approve accounts login on session loss (Claude Fable 5)
**Did:** added ACCOUNTS-001 to docs/ISSUES.md — `tg accounts login`
(interactive session (re)authorization) as deferred, pre-approved
maintenance work with re-entry trigger "first revoked or lost session";
noted the operational mitigation (back up `~/.config/tgcli/` and
`~/.local/state/tgcli/`).
**Decided:** the one future feature worth pre-approving is the session
recovery path — it is most needed exactly when it is least convenient to
build. Needs its own ADR at implementation time (auth touches the safety
surface).
**Learned:** nothing new.
**Next:** none; fires on trigger.
