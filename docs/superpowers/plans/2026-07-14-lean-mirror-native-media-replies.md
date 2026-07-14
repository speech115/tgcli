# Lean Mirror Native Media, Albums, and Replies Implementation Plan

**Status:** completed 2026-07-14. All task reviews and final local gates passed;
no live Telegram access or mutation was performed.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the one production `tg mirror sync` path so an unprotected
broadcast showcase can copy Telegram-native media, atomic albums, and mapped
reply relationships without silent flattening or partial confirmation.

**Architecture:** Keep ADR-0014's per-source SQLite store and native
`messages.forwardMessages(drop_author=True)` transport. Copy operations gain a
small durable batch identity/order so one Telegram album request is prepared
and confirmed as a unit. Replies are sent only when the source parent already
has a confirmed destination mapping. No renderer, downloader, second showcase
path, background process, or live Telegram fixture is added in this slice.

**Tech Stack:** Python 3.12, Telethon exactly 1.44.0, stdlib SQLite, pytest with mocked
Telegram clients.

## Global Constraints

- Supported source content in this slice is unprotected text, webpage previews,
  `MessageMediaPhoto`, and `MessageMediaDocument` (including Telegram's native
  video/audio/voice/sticker document variants). Captions travel with the native
  copy.
- Service messages and every other media wrapper fail closed before prepare,
  audit, or network dispatch. Protected sources/messages remain blocked.
- A non-null `grouped_id` is an album. Its complete contiguous source group is
  prepared before dispatch, copied in one `ForwardMessagesRequest`, and
  confirmed in one SQLite transaction. No album item advances the cursor alone.
- Sync remains streaming and does not pre-scan the complete channel history.
  If a previously completed `grouped_id` reappears non-contiguously, the reused
  segment blocks before its own prepare/audit/network work; earlier independently
  confirmed batches remain committed.
- Each album item owns one persisted signed 64-bit `random_id`; restart replays
  the same ordered ids and random ids.
- Telethon is pinned as `telethon==1.44.0` in project metadata and the lockfile
  because this slice relies on that schema's `ForwardMessagesRequest.reply_to`
  and `InputReplyToMessage` fields. A future pin change expires visual approval
  under ADR-0015 and must rerun this regression set.
- A source reply is never flattened. Its confirmed source-parent mapping must
  exist before preparation; otherwise sync exits 2 without audit or write for
  that child.
- Linked-discussion comments, forum topics, protected reconstruction, watch,
  deletion handling, reactions, views, and attribution emulation stay out of
  scope.
- The account mutation lock, durable FloodWait cooldown, fail-closed audit,
  Telethon mutation-safe session mode, and kill switches remain unchanged.
- Tests perform no live Telegram access or mutation and follow RED -> GREEN.

---

### Task 1: Durable atomic copy batches and parent lookup

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/tgcli/mirror/store.py`
- Modify: `tests/test_mirror_store.py`

**Interfaces:**
- `CopyOperation.batch_key: str`
- `CopyOperation.batch_index: int`
- `MirrorStore.prepare_batch(source_message_ids, *, batch_key, random_ids=None) -> list[CopyOperation]`
- `MirrorStore.confirm_batch(destination_message_ids: dict[int, int]) -> list[CopyOperation]`
- `MirrorStore.pending_batches() -> list[list[CopyOperation]]`
- `MirrorStore.destination_message_id(source_message_id: int) -> int | None`

- [x] **Step 1: Write failing store tests**

Add tests proving: a legacy database and partially migrated databases (only one
batch column present, nullable batch metadata, or a stale temporary rebuild
table) converge to the strict schema without losing mappings; NULL batch
metadata and uniqueness violations fail closed rather than being hidden; batch
prepare is atomic and preserves source order plus stable distinct random ids;
retrying the same batch is idempotent; conflicting membership/order is
rejected; batch confirmation requires an exact complete mapping and rolls back
every item plus the high-water cursor on any failure; duplicate destination ids
are rejected; repeating the same exact confirmation is idempotent; pending
batches retain order and reject a mixed confirmed/pending batch; destination
lookup returns only confirmed mappings.

- [x] **Step 2: Verify RED**

Run `uv run pytest tests/test_mirror_store.py -q`.

Expected: the batch interfaces and columns do not exist.

- [x] **Step 3: Implement the minimal migration and transaction rules**

First change project metadata to `telethon==1.44.0` and regenerate `uv.lock`.
Then migrate `copy_operations` inside one SQLite transaction by rebuilding it
through a disposable table with strict `NOT NULL`/`CHECK` constraints and
unique `(source_peer_id, batch_key, batch_index)`,
`(source_peer_id, random_id)`, and non-null destination-message ownership.
Drop any stale disposable table before the transactional rebuild. Backfill old
rows to `single:<source_message_id>` and index `0`, validate source/target row
counts, and rename only after the copy succeeds. The migration must be
idempotent when either new column already exists and must roll back to the
original table on any bad legacy row.

`prepare_copy` remains a compatible singleton wrapper. `confirm_copy` remains
a compatible wrapper around `confirm_batch`. Reject empty/duplicate ids,
duplicate random ids, random-id length mismatches, cross-batch confirmation,
incomplete mappings, changed batch membership/order, mixed confirmation state,
and conflicting destination ids.

- [x] **Step 4: Verify GREEN**

Run the focused command from Step 2. Expected: all store tests pass.

Then run `uv run pytest -q`. Expected: the full suite passes before commit.

- [x] **Step 5: Commit exact files**

Run `safe-commit "Add atomic mirror copy batches" pyproject.toml uv.lock src/tgcli/mirror/store.py tests/test_mirror_store.py`.

### Task 2: Native single media and explicit content policy

**Files:**
- Modify: `src/tgcli/commands/mirror.py`
- Modify: `tests/test_cli_mirror_sync.py`

- [x] **Step 1: Write failing content-policy tests**

Add tests proving one photo, one generic document, representative video/voice/
sticker documents, and one webpage preview use the existing native forward path:
the original source message id is forwarded, `drop_media_captions` is not
enabled, and no download or reupload call occurs. Mock tests do not claim that
Telegram rendered the caption correctly; that evidence belongs to the later
controlled-live visual gate. Prove a service message, paid media, story, poll,
unknown media wrapper, protected message, album, and reply fail before
prepare/audit/network until their respective task is implemented.

- [x] **Step 2: Verify RED**

Run `uv run pytest tests/test_cli_mirror_sync.py -q`.

Expected: supported media still exits with the old text-only policy.

- [x] **Step 3: Implement a type-based allowlist**

Replace the text-only guard with an explicit pinned-Telethon allowlist for no
media, `MessageMediaWebPage`, `MessageMediaPhoto`, and
`MessageMediaDocument`. Do not infer support from truthiness or attribute
order. Keep album and reply guards explicit so this intermediate commit cannot
flatten them.

- [x] **Step 4: Verify GREEN and regression**

Run:

```text
uv run pytest tests/test_cli_mirror_sync.py -q
uv run pytest tests/test_mirror_store.py tests/test_cli_mirror_sync.py -q
uv run pytest -q
```

- [x] **Step 5: Commit exact files**

Run `safe-commit "Copy native mirror media" src/tgcli/commands/mirror.py tests/test_cli_mirror_sync.py`.

### Task 3: Atomic albums, mapped replies, and restart recovery

**Files:**
- Modify: `src/tgcli/commands/mirror.py`
- Modify: `tests/test_cli_mirror_sync.py`

- [x] **Step 1: Write failing album and reply tests**

Cover: contiguous same-`grouped_id` (tested with `grouped_id is not None`, not
truthiness) messages become one ordered request; each
item has a distinct persisted random id; all confirmations are correlated
across supported update envelopes; missing/duplicate/extra confirmation leaves
the complete batch pending and the cursor unchanged; cancellation and restart
replay the same request; a pending album is recovered before new history even
when `get_messages(ids=[...])` returns shuffled objects; missing, duplicate, or
unexpected recovered source ids block the entire batch; a plain intra-channel
reply uses `InputReplyToMessage` with the mapped destination parent and copies
quote text/entities/offset when present; a missing or unconfirmed parent blocks
before child preparation/audit/write. For album replies, the header may exist
only on the oldest/leading item or be repeated with the same parent and quote
metadata; non-leading omissions are allowed. Conflicting parents/metadata, a
reply header appearing only after the leading item, cross-peer/forum/scheduled/
ephemeral reply shapes and mixed supported/unsupported album items block the
whole current batch before prepare/audit/network. A non-contiguous reuse of the
same grouped id blocks the reused segment before its prepare/audit/network work;
already confirmed prior batches remain committed so sync does not require a
full-history pre-scan. A change of grouped id flushes the previous valid album
without merging unrelated posts.

Also prove one album RPC produces exactly one fail-closed
`mirror-sync-forward` audit line containing the ordered source ids and random
ids. If audit append fails, the complete batch stays pending and Telegram is
not called.

- [x] **Step 2: Verify RED**

Run `uv run pytest tests/test_cli_mirror_sync.py -q`.

Expected: albums remain blocked and reply mapping is absent.

- [x] **Step 3: Implement ordered batch dispatch**

Recover `pending_batches()` first. Fetch every pending source id, index returned
objects by their actual positive `message.id`, verify the exact expected set
with no duplicates, then reconstruct request order strictly by persisted
`batch_index`; never trust Telegram's response order. For new history, buffer
only contiguous equal `grouped_id is not None` values and remember completed
group ids so a later non-contiguous reuse fails closed before preparing the
reused segment. Do not roll back or defer earlier confirmed batches merely to
pre-scan the rest of an unbounded history. Validate the entire current batch's
protection, media, reply shape/mapping, and membership before `prepare_batch`.

Extract a reply only from `MessageReplyHeader.reply_to_msg_id`; reject
cross-peer, forum, scheduled, ephemeral, todo, poll-option, or otherwise
unsupported reply shapes. Preserve supported quote fields in
`InputReplyToMessage`. Resolve one consistent reply parent per batch through
`destination_message_id`. Append one ordered batch audit record, then issue one
request using stored ordered source ids/random ids. Parse an exact one-to-one
requested-random-id confirmation set with unique destination ids, then call
`confirm_batch` once.

- [x] **Step 4: Verify GREEN and safety regressions**

Run:

```text
uv run pytest tests/test_cli_mirror_sync.py -q
uv run pytest tests/test_mirror_store.py tests/test_cli_mirror_init.py tests/test_cli_mirror_sync.py -q
uv run pytest -q
```

- [x] **Step 5: Commit exact files**

Run `safe-commit "Preserve mirror albums and replies" src/tgcli/commands/mirror.py tests/test_cli_mirror_sync.py`.

### Task 4: Contract, map, evidence, and release gate

**Files:**
- Modify: `docs/CONTRACT.md`
- Modify: `docs/MAP.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/DEVLOG.md`
- Modify: `docs/superpowers/plans/2026-07-14-lean-mirror-native-media-replies.md`

- [x] **Step 1: Document only shipped behavior**

Replace the text-only mirror contract with the exact native media allowlist,
batch/reply durability rules, JSON counters if changed, and remaining explicit
deferrals. Mark this plan completed only after all gates pass. MAP must describe
batch ownership in the store and orchestration ownership in the command.

- [x] **Step 2: Append truthful RED/GREEN evidence**

DEVLOG records exact focused RED failures, focused GREEN counts, full suite,
coverage, diff check, and review verdict. State explicitly that no live
Telegram access or mutation occurred.

- [x] **Step 3: Run final gates**

Run:

```text
uv run pytest tests/test_mirror_store.py tests/test_cli_mirror_init.py tests/test_cli_mirror_sync.py -q
uv run pytest -q
uv run python scripts/check-coverage.py
git diff --check
```

Expected: all focused and full tests pass, coverage reports 23 namespaces, and
the diff check is clean.

- [x] **Step 4: Independent whole-slice review**

Review the full diff against ADR-0014/ADR-0015 for silent omission, partial
album confirmation, reply flattening, duplicate risk, unsafe retry, and docs
drift. Any finding returns to RED -> GREEN before closeout.

- [x] **Step 5: Commit exact files**

Run `safe-commit "Document native mirror fidelity" docs/CONTRACT.md docs/MAP.md docs/PLAN.md docs/DEVLOG.md docs/superpowers/plans/2026-07-14-lean-mirror-native-media-replies.md`.

## Human Gate After This Plan

Do not immediately copy valuable real channels. After this plan is machine
green and reviewed, create or reuse one retained private source-only showcase,
fill it with the supported fixtures, create its destination through
`tg mirror init --commit`, copy through `tg mirror sync`, verify mappings and
native Telegram presentation, and ask the operator for `visual_approved`.
Comments, protected reconstruction, groups, and forums advance through their
own later production slices and visual gates; they are not implied by this
broadcast-media milestone.
