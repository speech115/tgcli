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


## 2026-07-17 — Clone deepening refactor (Claude Opus 4.8)
**Did:** executed the behavior-frozen clone deepening refactor plan
(`docs/superpowers/plans/2026-07-16-clone-deepening-refactor.md`). Extracted
`clone/batching.py` (pure batch planner: albums, service skips) and
`clone/transport.py` (pure forward/reupload/snapshot `decide()` →
`TransportPlan`) out of `commands/clone.py`'s `sync_text`; split poll/story
text rendering out of `fidelity.py` into `clone/snapshot.py`, leaving
`fidelity.py` with only media capability classification; deleted
`clone/profile.py` and inlined it as `_copy_profile` in `commands/clone.py`.
Added `tests/test_clone_attribution.py` and `tests/test_clone_replies.py` as
direct unit tests for the pure clone helpers. Full suite: 399 passed, 8
skipped. No changes to JSON output, exit codes, audit events, or the state
file schema.
**Decided:** do this deepening before starting round 3 (clone comments,
`docs/superpowers/specs/2026-07-16-clone-comments-design.md`) so the
discussion-loop work in round 3 can consume `batching.plan` and
`transport.decide` instead of duplicating the sync loop.
**Learned:** `commands/clone.py` has a hard 400-line budget; inlining
`_copy_profile` pushed it to 405, recovered by folding the single-caller
`_init_result` helper directly into `commit_init` and tightening
`_copy_profile`'s formatting. Round-2 topic-routing parameters (per-batch
destination topic / `reply_to` top id) threaded through the batch path
unchanged — `batching.Batch` stayed a plain message container, with topic
resolution left to orchestration in `commands/clone.py`.
**Next:** implement clone comments round 3 on top of the new
`batching`/`transport` interfaces.


## 2026-07-16 — Clone chat types round 2 live-accepted (Claude Fable 5)
**Did:** executed Task 9, the live acceptance gate for the round-2 clone plan.
Bot dialog `AnonAskBot` (39 messages): 34 native forwards + 5 prefixed reply
reuploads, order 1:1, both authors in forward headers, rerun 0. Owned legacy
basic group «ебка ютуба» (266 messages + 2 service): broadcast destination,
266/266 mapped ids strictly increasing, 40/40 reply links, 33 author-prefixed
reuploads, rerun 0. Forum gate ran on a purpose-built owned fixture
(«tgcli demo forum 07-16»: General + two named topics + in-topic reply):
forum-megagroup destination, `topics_created 2`, topics 1:1 with exact titles,
every message under its mirrored topic, the in-topic reply prefixed with the
right parent in the right topic, rerun 0 copies / 0 topics. Full numbers are
recorded under `### Live results` in the plan. Suite before acceptance:
`377 passed, 8 skipped`; compileall clean.
**Decided:** the task-7 forward-targeting question is closed on live data:
`ForwardMessagesRequest.top_msg_id` routes forwards into the correct
destination topics, so the designed reupload fallback stays unused and
ADR-0022 stands as written. All four retained destinations stay user-owned.
**Learned:** a SIGKILL between Telegram's send confirmation and the state
save reproduced the ADR-0022 crash model in the wild: the next sync blocked
fail-closed with `unexpected tail (1)`, and the documented manual repair
(delete the single unmapped destination message, rerun) resumed cleanly from
the saved cursor. Telethon's high-level `send_message(reply_to=<topic root>)`
is the working way to place fixture messages into forum topics
(`InputReplyToMessage` is rejected by `utils.get_message_id`), and
`GetForumTopicsRequest` lives under `functions.messages`, not
`functions.channels`.
**Next:** merge the round-2 branch (PR against main) — clone now covers
broadcast, megagroup, forum, basic, and dialog sources end to end.


## 2026-07-16 — Clone chat types round 2 documented (Codex)
**Did:** completed Task 8 of the round-2 clone plan. CONTRACT now records the
kind-dependent destination rule and `topics_created` output; ADR-0022 records
forum destination, topic-map, routing, recovery, and crash semantics; CLONE-002
is closed; MAP and PLAN match the implemented source kinds and module tree.
Task-level review passed after clarifying the original-plan wording and the
kind-neutral retry guarantee. A whole-branch architecture review then found and
TDD closed three prerequisite recovery-invariant gaps. Final verification is
recorded in the Task 8 completion commit.
**Decided:** forum live acceptance remains Task 9. Task 8 may claim mock/unit
support and an accepted contract, but not live Telegram routing evidence.
**Learned:** documentation review exposed broader implementation assumptions:
1:1 topic maps require destination-value uniqueness, source lookup must not
fabricate titles, and accepted-but-unconfirmed topic creation must turn an
unmapped destination service tail into a manual-repair gate.
**Next:** execute Task 9 live acceptance for bot, basic-group, and forum
fixtures; verify forum topic placement and a zero-copy rerun on Telegram.


## 2026-07-16 — Forum topic recovery invariants hardened (Codex)
**Did:** closed three final architecture-review findings with TDD. Persisted
and runtime topic mappings now exclude General, stay within signed TL-int
bounds, and remain one-to-one. Topic lookup requires one matching non-empty
title. Destination-tail verification blocks an accepted-but-unconfirmed topic
creation before a retry can duplicate it. Final gates passed `377 passed, 8
skipped`; coverage reports 23 namespaces.
**Decided:** an unmapped destination `MessageActionTopicCreate` is evidence of
an ambiguous accepted mutation and requires manual repair. Mapped topic-create
rows and all other service-only tail rows retain their existing acceptance.
ADR-0022 now records this stronger crash behavior.
**Learned:** treating every service row as harmless made the existing
service-tail exception too broad for lazy topic creation. The narrow action
classification preserves General/fresh-forum behavior without weakening
ordinary service-tail recovery. Final budgets are `clone.py 400/400`,
`topics.py 99/100`, `state.py 170/170`, and `attribution.py 78/80`.
**Next:** rerun the final architecture review; Task 9 live Telegram acceptance
remains separate and untouched.


## 2026-07-16 — Forum topic routing complete (Codex)
**Did:** completed Task 7 of the round-2 clone plan. Forum messages now resolve
their source topic, recover missing topic mappings from Telegram when needed,
and route native forwards, snapshots, protected reuploads, albums, and mapped
replies into the matching destination topic. General remains a passthrough.
Added mapped/general/unmapped, audit-failure, copy-retry, album-consistency,
malformed-header, cross-peer, and TL signed-int boundary regressions. Final
gates passed `362 passed, 8 skipped`; coverage reports 23 namespaces.
**Decided:** placement-only forum headers are topic placement, not replies, so
they stay eligible for native forwarding and do not count as flattened. Real
in-topic replies map the direct parent through `id_map` and add the mapped topic
through `topic_map`. All reply/topic IDs must be canonical positive signed TL
ints in `1..2_147_483_647` before lookup, audit, or mutation. Unsupported media
retains its established early report-and-skip semantics without reply routing.
**Learned:** retaining the source topic ID inside the validated reply signature
is necessary for album consistency; collapsing placement to `None` can hide
mixed-topic batches. The installed Telethon request supports native
`top_msg_id`, so the designed forced-reupload fallback was unnecessary. Final
budgets are `clone.py 400/400`, `topics.py 100/100`, `state.py 170/170`,
`attribution.py 78/80`, and `replies.py 76`.
**Next:** execute Task 8: document the shipped behavior in CONTRACT, ADR-0022,
ISSUES, MAP, and PLAN before live acceptance.


## 2026-07-16 — Lazy forum topic creation complete (Codex)
**Did:** completed Task 6 of the round-2 clone plan. Forum sync now turns
`MessageActionTopicCreate` service rows into destination topics, confirms the
Telegram-assigned root ID through `UpdateMessageID`, persists the topic map
before advancing the cursor, and reports `topics_created` in JSON and plain
output. The shared batch-confirmation parser moved into `clone/topics.py`.
Final gates passed `332 passed, 8 skipped`; coverage reports 23 namespaces.
**Decided:** topic creation is safe by construction: `create_topic` requires an
account alias and writes the fail-closed `clone-sync-topic` audit immediately
before dispatch. A replay with a saved topic mapping but stale cursor advances
without a duplicate audit or mutation. Only a hard crash after Telegram
creates the topic but before mapping persistence may duplicate one topic, as
accepted by the approved forum crash model.
**Learned:** pinned Telethon 1.44 exposes forum topic creation and lookup under
`functions.messages` with `peer=`, not the draft plan's `functions.channels`
spelling. The plan's Task 6/7 internal interfaces were corrected accordingly.
Final budgets are `clone.py 396/400`, `topics.py 70/100`,
`attribution.py 78/80`, and `state.py 170/170`.
**Next:** execute Task 7: route forum messages and replies into mapped topics.


## 2026-07-16 — Forum clone destinations complete (Codex)
**Did:** completed Task 5 of the round-2 clone plan. Forum megagroups are now
accepted as sources; init creates or recovers one private creator-owned
megagroup, enables forum mode idempotently, and records the destination before
the toggle so interrupted initialization is retryable. Sync requires an
enabled forum destination and now fails closed when the persisted destination
tail points past Telegram's current latest message. Added create, marker
recovery, audit-failure, source-kind drift, destination-shape, and tail-deletion
regressions. Final gates passed `328 passed, 8 skipped`; coverage reports 23
namespaces.
**Decided:** init may adopt a matching private owned plain megagroup and then
enable forum mode, while sync requires `forum=True`. Forum enablement has its
own fail-closed `clone-init-forum` audit record. A live source changing between
megagroup and forum is rejected before state writes, audit, or mutation.
**Learned:** Telethon 1.44 requires the `tabs` argument on
`ToggleForumRequest`; `tabs=False` preserves the default topic-list view. The
Task 5 safety additions consumed the remaining `commands/clone.py` budget, now
exactly `400/400`, so Task 6 must extract or compress before adding logic.
**Next:** execute Task 6: lazy destination-topic creation during forum sync.


## 2026-07-16 — Forum clone state foundation complete (Codex)
**Did:** completed Task 4 of the round-2 clone plan. `CloneState` now persists
`destination_kind` and a lazy `topic_map`, derives forum destinations from
forum sources, exposes topic mapping helpers, and includes topic IDs in the
destination-tail baseline. Added round-trip, legacy-default, malformed-state,
cross-field, mixed-map, Unicode-digit, and TL signed-int boundary regressions.
**Decided:** persisted forum state is fail-closed: source/destination kinds must
agree, only forum clones may have topics, and source/destination topic IDs must
be canonical positive ASCII decimal TL ints in `1..2_147_483_647`. This follows
the installed Telethon schema's signed `<i` serialization and prevents corrupt
state from disabling tail safety.
**Learned:** validating only `dict[str, int]` was insufficient: Python accepts
booleans as ints and Unicode decimal strings as numeric, while oversized values
cannot be serialized by Telegram. Three review loops converted those implicit
assumptions into tested state invariants. Final implementation budget is
`state.py 170/170`.
**Next:** execute Task 5: forum source gate and forum destination creation.


## 2026-07-16 — Clone chat types Tasks 1–3 final review fixes (Codex)
**Did:** corrected the init profile contract to include non-empty basic-group
descriptions and made migrated basic-group policy errors name the available
target channel id. Strengthened the CLI regression test with target id `555`.
RED: the focused policy matrix failed `1 failed, 2 passed`; GREEN: init tests
passed `19 passed`, and the final suite passed `298 passed, 8 skipped`.
**Decided:** this is a contract/error-detail correction within the approved
design; no new ADR or module change is required.
**Learned:** the first full run hit an unrelated timestamp-sensitive
`test_mirror_probe` redaction assertion because timestamp microseconds happened
to contain `999`; the isolated test and immediate full rerun passed.
**Next:** continue the approved round-2 plan from Task 4.


## 2026-07-16 — Clone chat types round 2 prepared (Claude Fable 5 / Codex)
**Did:** completed and approved the design for bot-dialog, legacy basic-group,
and forum-megagroup clone sources in
`docs/superpowers/specs/2026-07-16-clone-chat-types-2-design.md`, then wrote the
nine-task TDD implementation plan in
`docs/superpowers/plans/2026-07-16-clone-chat-types-2.md`. Codex recovered the
finished but untracked plan after Claude hit its usage limit, moved the design
commit off local `main` onto `codex/clone-chat-types-round-2-plan`, and restored
local `main` to `origin/main`.
**Decided:** bot dialogs follow the existing dialog path; live legacy basic
groups follow the attributed megagroup path into broadcast destinations; forum
sources create private owned forum-megagroup destinations with a lazy 1:1 topic
map. ADR-0022, CONTRACT, ISSUES, MAP, and PLAN changes remain Task 8 so project
documentation does not claim support before the implementation exists.
**Learned:** Claude's plan file was complete despite the interrupted response,
but it was not tracked and the design commit had been made directly on local
`main`. The implementation remains intentionally untouched at this preparation
checkpoint.
**Next:** execute tasks 1–9 in
`docs/superpowers/plans/2026-07-16-clone-chat-types-2.md`, preserving TDD and the
live Telegram acceptance gate.

## 2026-07-16 — Architecture review, deepening plan, mirror_probe archived (Claude Fable 5)
**Did:** ran an architecture review over the clone hot spot (report:
scratchpad HTML, not committed). Wrote
`docs/superpowers/plans/2026-07-16-clone-deepening-refactor.md` — behavior-
frozen refactor extracting `clone/batching.py` (pure batch planner) and
`clone/transport.py` (pure TransportPlan decision), splitting
`clone/snapshot.py` out of `fidelity.py`, inlining `profile.py`, and
backfilling direct unit tests for `attribution`/`replies`. Archived the
mirror-era diagnostic: deleted `src/tgcli/mirror_probe.py`,
`scripts/mirror_probe.py`, both probe test files; updated MAP.md and PLAN.md.
`pytest -q`: 274 passed, 8 skipped; `scripts/check-coverage.py`: OK.
**Decided:** deepen before round 3 — the comments loop must consume
`batching.plan`/`transport.decide` instead of duplicating the sync loop.
Refactor executes only after the round-2 chat-types branch merges (plan
Task 0 gates on it). CLI command-registration seam deferred to its own plan.
**Learned:** the three duplicated `limit` checks in `sync_text` are provably
one check (copied_batches only changes inside finish_batch);
`fidelity.replacement` non-None is exactly `fidelity.supports`, so the
transport decision is pure. attribution/replies/fidelity had zero direct
unit tests — everything ran through the 1092-line CLI fake.
**Next:** merge round 2 (`codex/clone-chat-types-round-2-tasks-1-3`), then
execute the deepening plan, then implement clone comments round 3.

## 2026-07-16 — Comments cloning designed as round 3 (Claude Fable 5)
**Did:** grilled the "clone channel comments" request end-to-end; wrote
`docs/superpowers/specs/2026-07-16-clone-comments-design.md` (approved design,
not implemented). Ran a read-only live probe (`channels.getFullChannel`,
`messages.getDiscussionMessage`, `messages.getReplies` via `tg api`) against a
real commented channel; findings recorded in the spec. No code changes.
**Decided:** scope = broadcast channel + linked discussion group, full-fidelity
(real linked megagroup with real threads, not snapshots); automatic when the
source has one, no flags; new clones only (auto-forward anchors cannot be
backfilled — existing clones re-init to opt in); two-phase sync with a second
(cursor, id_map) pair; unreadable source group → posts-only +
persistent `comments: unavailable` marker; attribution prefix amended globally
to an identify-the-author ladder (`"Name (@username): "` → text_mention on the
name → `"id N: "` → post_author → `"id unknown"`). Implementation sequenced as
round 3 after the round-2 branches merge (same files touched).
**Learned:** the anchor-timing constraint is the whole design: Telegram creates
the discussion auto-forward only if the group is linked *before* the post is
sent, so `setDiscussionGroup` must run at init, and retroactive comments are
impossible by construction. ADR-0015's live finding (forum ≠ discussion group)
cleanly separates this from round 2's topics work. Live probe gotchas: direct
comments carry `reply_to_top_id = null` (only nested replies set it); anchors
are recognized by `fwd_from.saved_from_peer + saved_from_msg_id`; channels
with direct messages expose `linked_monoforum_id`, which must not be mistaken
for `linked_chat_id`; comments with `from_id = null` occur in the wild.
**Next:** merge round 2 to main, then run writing-plans on the comments spec.

## 2026-07-16 — Megagroup and dialog clone sources live-accepted (Codex)
**Did:** executed the approved chat-types plan and added ADR-0021. Clone now
accepts non-forum megagroups and non-bot User dialogs, persists `source_kind`,
copies User bio/avatar during init, preserves native author headers on
non-replies, and reuploads mapped replies/protected content with an author-name
prefix and UTF-16-correct entity offsets. JSON/plain output now reports
`forwarded`, `reuploaded`, `snapshots`, and `reply_flattened`. TTL/view-once
media is reported unsupported. Reply validation moved to `clone/replies.py`;
source/author mechanics live in `clone/attribution.py`.

Live acceptance created retained private destinations for an owned 27-message
megagroup fixture, a 102-message organic megagroup, and two 11/53-message
dialogs. Readback proved strict order, two authors in the owned fixture and each
dialog, 30/30 organic-megagroup reply links, 4/4 dialog reply links, 4/4 visible
author prefixes, and zero-copy reruns. A live `MessageReplyStoryHeader` exposed
that Story replies have no message id; TDD now flattens and reports that shape
instead of blocking the remaining history.
**Decided:** attributed sources use a hybrid transport: native forward for
non-replies, prefixed reupload for mapped replies/protected content. Missing
direct parents and Story reply headers are explicit flatten fallbacks; a missing
top root no longer discards an available direct-parent link. Poll snapshots
remain the accepted ADR-0019 behavior rather than being rolled back based on a
stale review snapshot.
**Learned:** the external review correctly identified missing flatten reporting
but its poll/contract verdict predated commits `bf7e6f2` and `b4640a0`.
Telegram trims the trailing space from an empty prefixed caption (`Author: ` →
`Author:`), which is still a correct visible attribution. Final checks: `290
passed, 8 skipped in 2.20s`; coverage 23 namespaces; compileall and diff check
clean. Budgets: `390/150/91/43/74/63` lines for clone/state/fidelity/profile/
attribution/replies.
**Next:** commit, push `feature/clone`, and verify PR #8 CI.


## 2026-07-16 — Clone init copies channel profile (Codex)
**Did:** added ADR-0020 and the 37-line `clone/profile.py`. After destination
creation or recovery, `tg clone init --commit` now copies a non-empty source
description and a static source avatar before returning ready. All profile
network operations use persisted cooldown handling; mutations are audited, and
an avatar-download failure leaves the recorded destination retryable. TDD covers
description copy, avatar upload and temporary-file cleanup, the empty-profile
path, destination reuse, and the failure path. Applied the feature to the live
`@sral_v_nastav` clone: the source description was empty, while its previously
missing destination avatar was copied successfully.
**Decided:** title, available description, and static avatar are part of clone
initialization, not a later sync concern. Animated avatar motion is explicitly
outside the current fidelity guarantee.
**Learned:** channel descriptions use `messages.editChatAbout`, while channel
avatars use `channels.editPhoto`; Telegram re-encoded the live 640×640 JPEG but
the source/destination comparison remained visually identical (`SSIM 0.999885`).
Final checks: `271 passed, 8 skipped in 1.88s`; coverage 23 namespaces;
compileall and diff check clean. Budgets remain `400/149/89`, with the new
profile helper at 37 lines.
**Next:** run the final gate, commit, push `feature/clone`, and verify PR #8 CI.


## 2026-07-16 — Clone fallbacks made human-readable (Codex)
**Did:** redesigned ADR-0019 destination messages after user visual review.
Poll snapshots now use a Russian heading, Unicode progress bars, natural vote
pluralization, percentages, and a single human total; timestamps and technical
labels were removed. Story placeholders now contain only `Stories недоступна`
and `Автор: <name>`, with the author encoded as a clickable Telegram text URL;
Story IDs were removed. TDD verifies both poll modes, bar output, Russian vote
forms, UTF-16 entity offsets, user/channel links, and reply continuity.
Edited the four existing destination messages in place (`659`, `720`, `728`,
`741`) through the audited mutation path, so their IDs and reply `701 → 659`
remain unchanged.
**Decided:** fallback content is a human-facing channel post, not an operational
report; diagnostics belong in state/audit, never in destination text.
**Learned:** Telegram text links require UTF-16 offsets, while fractional block
characters make close results such as 51/49 visually distinct without images.
Final checks: `268 passed, 8 skipped in 1.99s`; coverage 23 namespaces;
compileall and diff check clean. Budgets remain `400/149/89` lines.
**Next:** run the full gate, commit, push `feature/clone`, and verify PR #8 CI.


## 2026-07-16 — Poll and Story fallbacks repaired a live clone (Codex)
**Did:** added ADR-0019 and a 73-line `clone/fidelity.py`. Polls now become
timestamped static result snapshots; Story references become placeholders with
resolved author title/name, username, and Story ID. Both are mapped and audited,
so later replies target them. Added nested reply-root support and a content-first
fallback when no parent mapping exists. TDD covered single/multiple-choice poll
snapshots, user/channel Story labels, Story reply mapping, nested roots, and
missing-parent flattening. A live poll canary proved native copies reset 80 and
89 voters to zero. For clone `4fa28c…`, backed up state, removed only its mapped
destination suffix `326…658`, rewound source cursor to `336`, and replayed it.
The repaired clone reached cursor `682`, 662 mappings, zero unsupported rows,
and a zero-copy rerun. Readback verified strict order, no forward attribution,
both poll snapshots, both named Story placeholders, and reply `379` mapped to
Story placeholder `337`.
**Decided:** static poll results are more truthful than a native zero-vote copy;
expired Stories retain an explicit named position instead of disappearing.
`clone.py` remains exactly 400 lines; the new fidelity helper has a 100-line
budget.
**Learned:** `drop_author=True` hides attribution but Telegram still creates a
fresh poll identity and discards vote counts. A mapped placeholder is sufficient
to preserve the later reply graph even when the original Story bytes are gone.
Final checks: `268 passed, 8 skipped in 3.17s`; coverage 23 namespaces;
compileall and diff check clean.
**Next:** run the final post-documentation gate, commit, push `feature/clone`,
and confirm PR #8 CI.


## 2026-07-15 — Frozen mirror product removed (Codex)
**Did:** completed Task 9 after the Task-8 live gate. Removed the `tg mirror`
parser/dispatch, `commands/mirror.py`, the SQLite `mirror/` package, and all
three product test files. Added a public-CLI regression proving `mirror` is no
longer a command. Deleted the legacy CONTRACT appendix and synchronized README,
SKILL, FEATURES, MAP, PLAN, the clone checklist, fixture wording, and
superseded-document banners. Net change before this entry: 51 insertions and
5,400 deletions. Final checks: `263 passed, 8 skipped in 2.13s`; `coverage OK:
23 namespaces`; compileall and `git diff --check` clean. A clean wheel contains
`commands/clone.py` and the independent `mirror_probe.py`, but no mirror command
or package.
**Decided:** historical ADRs/plans/DEVLOG remain as decision evidence. The
independent read-only protected-content probe and demo-channel seeder remain,
as required by the clone design. Existing local mirror SQLite files and
user-owned Telegram destinations are not automatically deleted.
**Learned:** filename-based deletion would have incorrectly removed the probe;
the product boundary is the command/store surface, not every path containing
the word `mirror`.
**Next:** complete branch review and publish the final Task-9 commit to draft
PR #8; merging remains a separate user decision.


## 2026-07-15 — Clone live acceptance completed (Codex)
**Did:** completed Task 8 on account `main` against the controlled Stage-2
sources. Open source `3928214505` cloned 12/12 content messages to retained
destination `3837236912`; protected source `4373370262` cloned 12/12 to
retained destination `4341258020`. Both runs skipped one channel service row,
reported zero unsupported kinds, reached cursor 13, and immediate reruns copied
zero. Independent raw history comparison passed for content/media order,
mapped reply parent, album grouping, and document MIME/attribute shapes.
**Decided:** accepted ADR-0018 after the first open sync safely exposed a live
baseline mismatch: channel creation plus init title edit produce two service
rows. Tail verification now accepts service-only rows but still blocks any
ordinary unexpected message before source scan, audit, or copy.
**Learned:** a fixed numeric fresh-channel baseline is not stable across the
real create-and-title workflow; classifying the visible tail preserves the
safety boundary without blocking harmless Telegram metadata events. The TDD
regression failed with the live exit-2 shape before the fix and passed after it.
Final local checks: `429 passed, 8 skipped in 3.41s`; `coverage OK: 23
namespaces`; compileall and `git diff --check` clean. Budgets remain
`clone.py` 400 lines and `clone/state.py` 149.
**Next:** execute Task 9: delete the frozen mirror parser, implementation,
tests, and legacy CONTRACT appendix, then run the full cutover gate.


## 2026-07-15 — Clone contract made canonical (Codex)
**Did:** completed Task 7. Reassigned CONTRACT.md §11 to the full `tg clone`
surface, removed implementation-task wording, and demoted `tg mirror` to an
explicit frozen legacy appendix retained only until post-acceptance deletion.
Updated MAP, PLAN, and the clone implementation checklist to show Tasks 1–7
complete and live acceptance pending. Final local checks: `428 passed, 8
skipped in 3.56s`; `coverage OK: 23 namespaces`; compileall and `git diff
--check` clean.
**Decided:** documentation now treats clone as canonical before live acceptance,
while mirror remains available as a rollback reference. Task 9 deletion still
cannot happen before the Task 8 live gate required by ADR-0017.
**Learned:** preserving the legacy contract as an unnumbered appendix avoids a
false dual-product contract without removing the rollback evidence too early.
**Next:** execute Task 8 against controlled live demo channels, verify exact
source order visually, then confirm an idempotent rerun reports zero copied.


## 2026-07-15 — Clone replies and protected reupload implemented (Codex)
**Did:** implemented Task 6 by TDD. Reply-bearing batches now reconstruct with
the persisted destination parent and supported quote metadata instead of using
Telegram's reply-dropping native forward. Protected sources/messages reupload
text, webpage previews, photos, documents, and albums; captions/entities,
document MIME/attributes, album order, and per-item confirmation are retained.
Temporary downloads are cleaned, failed downloads leave state retryable before
audit/write, and upload/send/download FloodWait persists cooldown. Added the
live-proven `UpdateShortSentMessage` confirmation path. Updated CONTRACT, MAP,
PLAN, and the clone checklist. Final local checks: `428 passed, 8 skipped in
3.53s`; `coverage OK: 23 namespaces`; compileall clean. Budgets: `clone.py`
399 lines, `clone/state.py` 149, all clone mocked tests 1096.
**Decided:** Task 6 carries ADR-0016 forward unchanged: every reply batch uses
reupload even on an open channel; ordinary open non-replies remain native.
No new module or architecture was introduced.
**Learned:** reconstruction has two distinct valid confirmation envelopes;
rejecting `UpdateShortSentMessage` would falsely block protected text after a
successful Telegram send. Download failure must happen before the mutation
audit because no upload/send has been attempted yet.
**Next:** execute Task 7 contract cleanup, then Task 8 controlled live clone
acceptance before deleting frozen mirror code.

## 2026-07-15 — Clone native media and albums implemented (Codex)
**Did:** implemented Task 5 by TDD. `tg clone sync` now natively forwards the
explicit webpage/photo/document media allowlist with captions intact, buffers
contiguous albums by `grouped_id is not None` (including zero), preserves their
position, sends each album in one request, and saves the complete mapping only
after exact unique confirmations for every item. Added `docs/ISSUES.md` with a
durable post-v1 poll-reconstruction item and live re-entry criteria. Updated
CONTRACT, MAP, PLAN, and the clone checklist. Final local checks: `419 passed,
8 skipped in 3.41s`; `coverage OK: 23 namespaces`; `clone.py` is exactly 400
lines and `clone/state.py` is 149 lines.
**Decided:** polls stay skip-and-report in v1, but their future reconstruction
is now explicit rather than buried in the implementation plan. Native album
confirmation is all-or-nothing in local state.
**Learned:** a dictionary-based confirmation matcher can hide duplicate
`UpdateMessageID` rows; counting the raw confirmation envelope first prevents
partial or ambiguous album acceptance.
**Next:** implement Task 6 reply mapping and protected-content reupload using
the frozen mirror transports, then run the full clone contract suite.

## 2026-07-15 — Clone text sync implemented (Codex)
**Did:** implemented Task 4 `tg clone sync` by TDD: oldest-first plain-text
forwarding, exact confirmation before per-message state save, idempotent cursor
resume, destination tail verification, service/unsupported skip reporting,
positive `--limit` with `more`, readonly preflight, mutation-safe sessions,
fail-closed audit, and persisted FloodWait cooldown. Kept future-supported
media/albums/replies/protected content behind a controlled block without cursor
advance until Tasks 5–6, preventing draft-state data loss. Updated CONTRACT,
MAP, PLAN, and the implementation checklist. Final local checks: `415 passed,
8 skipped in 3.17s`; `coverage OK: 23 namespaces`; compileall clean.
**Decided:** Task 4 may permanently skip only content outside the final clone
allowlist; content awaiting a later planned transport must remain retryable.
**Learned:** treating all not-yet-implemented media as unsupported would move
the cursor past content that Task 5 is supposed to copy.
**Next:** implement Task 5 media and contiguous album batches, preserving their
source position and treating `grouped_id=0` as a real album id.

## 2026-07-15 — Clone init implemented; Tasks 1–3 reconciled (Codex)
**Did:** reconciled the first two clone tasks with their contracts, trimming
`clone/state.py` to 149 lines and making incomplete JSON state fail closed.
Implemented `tg clone init` by TDD: read-only preview, single-use commit,
mutation-safe destination creation, durable marker recovery, recorded-
destination reuse, wrong/multiple-marker blocking, fail-closed audit, readonly
preflight, and persisted FloodWait cooldown. Updated CONTRACT, MAP, PLAN, and
the approved design status/order. Final local checks: `406 passed, 8 skipped in
3.26s`; `coverage OK: 23 namespaces`; compileall clean.
**Decided:** no new architecture; Task 3 follows ADR-0017 and reuses the shared
preview/audit/session safety primitives. Clone remains broadcast-only in v1.
**Learned:** recorded destinations must bypass marker recovery or a repeated
init can create a duplicate; syntactically valid but incomplete state needs the
same controlled PolicyError as malformed JSON.
**Next:** implement Task 4, oldest-first text sync with tail verification,
skip reporting, per-batch state saves, cooldown enforcement, and `--limit`.

## 2026-07-15 — Clone rewrite planned; mirror frozen; repo hygiene (Claude Fable 5 / Opus 4.8)
**Did:** designed `tg clone` to replace the over-built `tg mirror`
(1,793 prod lines ≈ entire rest of core). Wrote+committed
[clone design spec](superpowers/specs/2026-07-15-clone-design.md) and
[ADR-0017](decisions/ADR-0017-clone-supersedes-mirror.md). Fast-forwarded main
to the completed Stage-2 live demo. Repo cleanup so mirror work can't be
confused with clone: tagged lab branches `archive/mirror-r1-controlled-lab`
and `archive/mirror-aggregate-checkpoints` (pushed to origin), deleted all five
mirror branches locally + the two lab worktrees; superseded banners on the five
active mirror plans and ADR-0013–0016; frozen banner in `commands/mirror.py`;
MAP/PLAN updated.
**Decided (with user):** clean rewrite with organ transplant (transports +
batch validation move from mirror.py, mirror deleted last after live
acceptance); v1 = full live-proven parity; JSON state file (no SQLite, no
migrations, no own locks — session.py flock suffices); safety.py preview→commit
for init (no custom --confirm); tail-verification crash model (≤1 duplicated
batch, replaces ~500 lines of random_id/exact-confirm machinery); unsupported
kinds skip+report (not fatal — fixes mirror's permanent-block bug);
`clone status` day one; `sync --limit N`; budgets clone.py ≤400 / state.py ≤150
/ tests ≤~1200. Work on `feature/clone`, PR to main; Claude implements, plan
self-contained so Codex can take over.
**Learned:** mirror bloated by building parallel infra instead of reusing core
(own confirmation, own lock redundant with session.py, own cooldown store) +
guarantees inappropriate for owner-operated CLI + migrations for its own dev
history + no status window. The 07-13 showcase sources were unusable because
mirror fail-closed on unsupported kinds mid-history — clone's skip+report fixes
this.
**Next:** delete remote mirror branches on origin, then run
superpowers:writing-plans to produce the task-by-task clone implementation plan.

---

## 2026-07-15 — Stage 2 completed: live open/protected production mirrors (Codex)
**Did:** finished the two production-path demo syncs and independently read
source/destination histories. Protected source `4373370262` reached retained
destination `3978334039` with 12 copied messages, one skipped service row, and
cursor 13. The first open destination exposed that Telegram accepted native
`ForwardMessagesRequest(reply_to=...)` but dropped the actual reply link. Added
a failing regression, routed only reply-bearing open batches through the
existing reconstruction transport, and documented the correction in ADR-0016.
Created a fresh open source `3928214505` and retained destination `4331196578`;
all 12 supported messages arrived, the reply maps to the copied parent, the
album remains grouped, and cursor reached 13. Immediate reruns copied zero for
both topologies. The earlier open pair (`4411329645` → `4342979899`) remains a
retained diagnostic artifact and is not an approval candidate. Preserved the
five frozen TSV columns by appending `skipped_service`, and removed the
unshipped one-off channel-creation helper after review found that it lacked the
durable reconciliation and kill-switch guarantees of `mirror init`.
**Decided:** service actions skip-and-count; reply-bearing batches reconstruct
even for open sources; ordinary open content remains native-forwarded. Demo
source creation remains an operator action rather than a shipped unsafe helper.
**Learned:** a valid raw request shape is not sufficient live fidelity evidence:
Telegram can accept `reply_to` on native forwarding while omitting the reply in
the resulting broadcast message. Source/destination reads are a required gate.
**Next:** user performs side-by-side visual review of the verified open and
protected pairs; only then may either topology become `visual_approved`.

## 2026-07-15 — Stage 2: live demo pair + service-message skip (Claude Fable 5)
**Did:** pushed Stage 1 main to origin; added
`scripts/create_demo_channel.py` (private broadcast creation with optional
`--protected` noforwards toggle); created and seeded a fresh demo pair with
mirror-supported kinds only (`tgcli demo open 07-15` id 4411329645,
`tgcli demo protected 07-15` id 4373370262, msgs 2–13 each). First real
`mirror sync` immediately hit the channel-creation service message at id 1
and blocked — fixed by skipping service messages in the new-history scan and
reporting a per-run `skipped_service` count in the sync JSON/plain output
(`src/tgcli/commands/mirror.py`, CONTRACT §11 updated, TDD:
`test_mirror_sync_skips_service_messages_and_reports_them`).
**Decided:** service messages are structural noise present in every real
channel, so they skip-and-count instead of failing closed; all other
non-allowlisted content still blocks. The 07-13 showcase sources were left
untouched (they contain unsupported kinds mid-history and reseeding would
duplicate content), hence the fresh pair.
**Learned:** every fresh channel starts with a service action at message
id 1, so the previous fail-closed rule made real-channel sync impossible from
the very first message; the lab transport had always skipped these, the lean
product path never did.
**Next:** run `mirror init --commit` + `sync` for both demo channels and hand
the two source/clone pairs to the user for side-by-side visual acceptance.

## 2026-07-15 — Stage 1: mirror branches consolidated into main (Claude Fable 5)
**Did:** fast-forwarded `main` to `codex/mirror-showcase-product` (which
already contained the lean branch) after a green 374-test run; ported the
lab-proven protected download→reupload transport into `mirror sync`
(`src/tgcli/commands/mirror.py`): protected batches are reconstructed via
raw `sendMessage`/`sendMedia`/`uploadMedia`+`sendMultiMedia` with the same
journaled random ids, strict confirmations, cooldown recording, and a
`mirror-sync-reupload` audit action; added
`scripts/seed_demo_channel.py` (manual demo seeding with ffmpeg fixtures,
owned-private-broadcast guard); updated CONTRACT/FEATURES/MAP. 383 tests
pass.
**Decided:** transport is chosen per batch (`noforwards` on the channel or
any message → reupload), no CLI flag; kept the media allowlist identical
for both transports; no new ADR per the agreed lean process — the plan doc
is `docs/superpowers/plans/2026-07-15-protected-reupload-transport.md`.
Lab branches stay archived, their 25k-line test suites are intentionally
not ported.
**Learned:** `messages.sendMessage` can confirm via `UpdateShortSentMessage`
(no `UpdateMessageID`), which the strict confirmation matcher now handles;
`sendMultiMedia` requires `uploadMedia` per item first — uploaded media
cannot go into `InputSingleMedia` directly.
**Next:** Stage 2 — seed a demo channel pair and run the first visual
acceptance (channel topology, open + protected) with the user.

## 2026-07-14 — Native mirror fidelity slice completed (Codex GPT-5)
**Did:** completed and documented the Telethon-1.44 native mirror slice without
live Telegram access. Task 1 added strict atomic copy batches and parent lookup:
its initial RED was `20 failed, 20 passed`; review-driven REDs were
`7 failed, 46 passed`, `2 failed, 53 passed`, and `1 failed, 55 passed`; the
final store suite was `56 passed`. Task 2's explicit media-policy RED was
`9 failed, 24 passed` and focused GREEN was `33 passed`. Task 3's album/reply
RED was `14 failed, 51 passed` and focused GREEN was `66 passed`. The accepted
Task 1, Task 2, and Task 3 reviews all returned PASS. Whole-slice review then
caught unhandled local-store `ValueError`; its end-to-end boundary RED was
`3 failed, 66 passed` and final sync GREEN was `69 passed`. The corrected
whole-slice re-review returned SHIP. Updated CONTRACT, MAP, PLAN, and the
implementation plan to describe only shipped behavior.
**Decided:** `sync` natively forwards only unprotected text, webpage, photo,
and document wrappers; contiguous albums are one prepared/audited/dispatched/
confirmed batch with distinct persisted random ids; plain intra-channel
replies require a confirmed mapped parent and preserve supported quote fields.
No download/reupload path was added. Unsupported, protected, service, media,
and reply shapes fail closed, while linked comments, watch, protected
reconstruction, groups, and forums remain deferred.
**Learned:** the streaming backfill cannot pre-scan unbounded history. A
non-contiguous reuse of a completed `grouped_id` therefore blocks the reused
segment before its own prepare/audit/network work while previously confirmed
batches remain committed. Cross-run reuse and malformed store state must also
cross the CLI boundary as structured `BLOCKED`, never a Python traceback. This
preserves fail-closed behavior and useful streaming progress.
**Checks:** mirror-focused tests reported `157 passed in 1.52s`; the full suite
reported `374 passed, 8 skipped in 2.95s`; the namespace gate reported
`coverage OK: 23 namespaces`; `git diff --check` exited 0 with no output. All
tests used local fakes or mocked clients; no Telegram connection or mutation
was attempted.
**Next:** run the controlled source-only showcase and visual gate only with
explicit operator approval; do not copy valuable real channels first.

## 2026-07-14 — Cooldown concurrency regression made effective (Codex GPT-5)
**Did:** fixed the whole-branch re-review finding in the test only; production
code was unchanged. The previous concurrency test patched public
`cooldown_deadline()`, while `record_cooldown()` actually calls private
`_read_cooldown()`, so its forced race was inert. The replacement starts two
writers through a barrier and instruments every `_read_cooldown()` with a
non-blocking flock probe that proves the account cooldown lock is already
held. With `_cooldown_write_lock` temporarily replaced by a no-op, the exact
test RED was `1 failed in 0.06s` with two
`cooldown read ran outside the write lock` errors. Restoring the real lock made
the same test GREEN: `1 passed in 0.05s`.
**Decided:** the regression verifies the synchronization boundary directly,
then verifies concurrent compare-and-max retains the later deadline. It does
not rely on thread timing or patch an unused public reader.
**Learned:** a concurrency test can look deterministic yet test nothing when
its synchronization hook is off the production call path. Lock ownership at
the actual read boundary is the stronger invariant.
**Checks:** `uv run pytest tests/test_mirror_store.py -q` reported
`20 passed in 0.17s`; `uv run pytest -q` reported
`287 passed, 8 skipped in 1.83s`; `git diff --check` exited 0 with no output.
No Telegram connection or mutation was attempted.
**Next:** independently re-review the corrected regression, then publish the
branch before any controlled live showcase mutation.

## 2026-07-14 — Mirror mutation transport and account locks hardened (Codex GPT-5)
**Did:** closed every whole-branch review blocker without Telegram access.
Added a mutation-only Telethon session mode (`request_retries=0`,
`flood_sleep_threshold=0`), a hashed resolved-account mutation lock shared by
init commit and sync, serialized cooldown compare-and-max with parent-directory
fsync, and fail-closed retry-flag validation for authorized mirrors. The first
combined RED run was `13 failed, 65 passed in 0.84s`; failures covered each
missing behavior. Pinned Telethon 1.44 fake-sender tests now prove one send on
an ambiguous timeout and no sleep/retry for a five-second FloodWait. A
two-client/two-alias concurrency test proves exactly one create dispatch for
the same Telegram user id.
**Decided:** read-oriented commands and init preview retain Telethon defaults;
only mirror mutations opt into the no-retry transport. The non-blocking
account-user-id lock starts immediately after source/account resolution and
stays held through cooldown enforcement, reconciliation, audit, Telegram
mutation, and durable confirmation. Cooldown persistence uses a separate
blocking lock so it remains safe when called inside the broader mutation lock.
Retry flags are paired before session acquisition, then checked against the
exact mirror id before authorized handling; every retry form is inapplicable
once authorization exists.
**Learned:** serializing local session names is insufficient because aliases
can share one Telegram identity. Atomic `os.replace` is also insufficient for
both concurrency and crash durability: compare-and-max must happen under the
same lock, and the containing directory must be fsynced after replacement.
**Checks:** focused safety suites: `78 passed in 0.67s`; full suite:
`287 passed, 8 skipped in 2.10s`; namespace gate:
`coverage OK: 23 namespaces`; `git diff --check` exited 0 with no output; CLI
help exposed `--retry-create` and `--confirm MIRROR_ID`. All tests used local
fakes or mocked clients; no Telegram connection or mutation was attempted.
**Next:** review and publish this branch before any controlled live showcase
mutation.

## 2026-07-14 — Mirror init recovery and cooldown documented (Codex GPT-5)
**Did:** closed the destination-creation safety plan in Tasks 1–4 without live
Telegram access. Task 1 store RED was `6 failed, 12 passed in 0.17s`; focused
GREEN was `18 passed in 0.18s`, and its full regression was
`261 passed, 8 skipped in 1.83s`. Task 2 init RED was
`13 failed, 9 passed in 0.46s`; focused GREEN was
`22 passed in 0.28s`, init plus store was `40 passed in 0.42s`, and its full
regression was `271 passed, 8 skipped in 1.90s`. Task 3 init RED was
`2 failed, 23 passed in 0.38s` and sync RED was
`1 failed, 17 passed in 0.36s`; GREEN was `43 passed in 0.45s` for init plus
store, `61 passed in 0.53s` for all mirror safety suites, and
`275 passed, 8 skipped in 2.07s` for the full regression.
**Decided:** `reconcile_required` is durable before a create dispatch; an
ambiguous zero-match recovery can create again only with `--retry-create` and
an exact `--confirm MIRROR_ID`. Wrong-shape or multiple exact-marker matches
become `blocked`. Authorization atomically records `authorized` plus
`user_owned_retained`. Create, title-edit, and copy FloodWaits share a hashed,
account-level UTC cooldown under `TGCLI_STATE_DIR/mirrors/cooldowns/`; active
cooldown returns exit 5 before mutation and never triggers internal sleep or
retry. Authorized destinations are retained and never removed by interruption,
timeout, signal, process failure, or review expiry.
**Learned:** creation reconciliation and FloodWait handling need one account
gate but two durability boundaries: per-source SQLite state preserves the
ambiguous create, while the hashed account JSON blocks every source sharing
that account. These changes do not implement media, comments, watch, forum
parity, or showcase promotion.
**Checks:** final mirror safety gate:
`61 passed in 0.63s`; full suite:
`275 passed, 8 skipped in 2.08s`; namespace gate:
`coverage OK: 23 namespaces`; `git diff --check` exited 0 with no output;
`uv run tg mirror init --help` exited 0 and exposed both `--retry-create` and
`--confirm MIRROR_ID`. All tests used mocked clients; no Telegram connection or
mutation was attempted.
**Next:** independently review this safety slice before any controlled live
showcase mutation, then implement native media, albums, and mapped replies.

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

## 2026-07-13 — Public-channel analysis workflow verified (Codex)
**Did:** verified that `@ivankhalilov` resolves through the live `main` account,
read a bounded five-message sample, and checked the Phase 5 export implementation
and contract for a complete oldest-first JSONL corpus. No bulk export or Telegram
mutation was performed.
**Decided:** use `tg --readonly export messages` for the source corpus, then a
separate evidence-backed coding pipeline; media inspection is a second pass for
selected posts rather than part of the initial full export.
**Learned:** the existing export preserves message ids, dates, text, media type,
and reply ids, which is sufficient for a text-first longitudinal analysis but
not for claims about the contents of attached media.
**Next:** run the full export only after the user asks to execute the analysis.

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
