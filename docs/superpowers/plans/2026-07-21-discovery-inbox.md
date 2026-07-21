# Discovery & Inbox Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent resolve peers, survey a chat's media, follow a real conversation thread, and keep its own inbox in order (ADR-0029).

**Architecture:** Three independently shippable, contract-additive slices. Slice 1 adds the read-only identity layer (`resolve`, `contacts`). Slice 2 adds direct inbox mutations (`mark-unread`, `dialog pin/unpin`) reusing ADR-0028 gating, no preview. Slice 3 adds the read-only discovery commands (`media manifest`, `thread`).

**Tech Stack:** Python 3.12, Telethon, argparse, pytest. No new dependencies.

## Global Constraints

- ADR-0026/0029: this plan is the approved scope; nothing beyond it.
- Contract discipline (AGENTS.md): any task that changes CLI flags, JSON shapes, or TSV columns updates `docs/CONTRACT.md` **in the same commit**. All JSON here is additive; TSV columns append at the end only.
- stdout is contract data only; everything else to stderr.
- TDD per task: failing test → minimal code → green → commit.
- Gates before every commit: `pytest -q`, `ruff check .`, `ruff format --check .`, `pyright` (at least `pytest -q` per step; all four at each task's commit).
- Do NOT touch `src/tgcli/clone/` (frozen) or the ADR-0028 message-mutation preview path.
- Layering: `commands/<name>.py` owns logic + `to_rows`; `cli.py` owns parsing, safety gating, and dispatch. Reuse `read.message_to_dict`, `read._dialog_name`, `read.sanitize_plain_text`.
- Exit codes (errors.py): 1 generic, 2 PolicyError, 3 ConfigError, 4 NotFoundError, 5 RateLimitError.
- Mutations acquire the client the same way `mark-read` does (see `cli.py` `mark-read` branch) and call `safety.enforce_mutation_allowed(args.readonly)` before running; each writes an audit record.
- Each slice ends with a DEVLOG entry and is mergeable on its own.

---

## Slice 1 — Identity layer (Tasks 1–3)

### Task 1: Allowlist `contacts.resolvePhone`

**Files:**
- Modify: `src/tgcli/commands/api.py` (`READ_METHOD_ALLOWLIST`)
- Test: `tests/test_commands_api.py`

**Interfaces:** adds `"contacts.resolvePhone"` to the read allowlist so the identity layer (and raw `tg api`) can call it. No behavior change beyond that method becoming an allowed read.

- [ ] **Step 1: Failing test** — assert `api.is_read_method("contacts.resolvePhone")` is `True` (match the file's existing allowlist test style).
- [ ] **Step 2: Verify failure** — `pytest tests/test_commands_api.py -q`.
- [ ] **Step 3: Implement** — add the one entry to the frozenset, keeping alphabetical order (after `contacts.resolveUsername`).
- [ ] **Step 4: Gates + commit** — `git commit -m "Allowlist contacts.resolvePhone as a read method"`.

### Task 2: `tg resolve`

**Files:**
- Create: `src/tgcli/commands/identity.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`
- Test: `tests/test_cli_resolve.py`

**Interfaces:**
- Produces (module `identity`): `resolve(tg, ref: str) -> dict` returning `{"peer": {"id", "type", "username", "display_name", "is_contact", "is_bot"}}`. `type` ∈ `user|bot|group|channel`. Helper `peer_to_dict(entity) -> dict` (reused by `contacts` in Task 3). Input dispatch: a value matching `+<digits>` uses `functions.contacts.ResolvePhoneRequest`; everything else goes through `chatref.parse` + `tg.get_entity` (covers @username, t.me links, numeric ids). Phone that resolves to nothing raises `NotFoundError`. **Never** call `importContacts`.
- CLI: `tg resolve REF` (read; no `--readonly` gate needed).

- [ ] **Step 1: Failing tests** in `tests/test_cli_resolve.py`:
  - `resolve @user` → peer dict with `type == "user"`, `is_bot == False`, `is_contact` reflected from the entity.
  - `resolve` a bot entity → `type == "bot"`, `is_bot == True`.
  - `resolve +99512345678` → FakeClient records a `ResolvePhoneRequest` (assert the raw request type/phone) and returns a user peer; assert **no** `importContacts` call is made.
  - `resolve +99500000000` with an empty phone result → exit 4 (`NotFoundError`).
  - Plain output (`--plain`) → single row `(id, type, username, display_name)`.
  Extend `tests/conftest.py` `FakeClient` with a `__call__` branch (or a `resolve_phone` recorder) returning a `contacts.resolvedPeer`-shaped namespace, and record calls so the "no importContacts" assertion is checkable.
- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_resolve.py -q` (unknown command).
- [ ] **Step 3: Implement** `identity.py`:
  - `_is_phone(ref)`: `ref.lstrip().startswith("+")` and the rest is digits.
  - `peer_to_dict(entity)`: derive `type` from `getattr(entity, "bot", False)` → `bot`; `broadcast` → `channel`; `megagroup`/`chat` → `group`; else `user`. `display_name` from first+last or title. `is_contact` from `getattr(entity, "contact", False)`; `is_bot` from `getattr(entity, "bot", False)`.
  - `resolve`: phone → `ResolvePhoneRequest(phone=digits)` then map the returned peer via the users/chats in the response (raise `NotFoundError` if empty); else `tg.get_entity(chatref.parse(ref))` wrapped so `ValueError` → `NotFoundError`.
  - `to_rows(data)` → `[(peer["id"], peer["type"], peer["username"], peer["display_name"])]`.
  `cli.py`: `p_resolve = sub.add_parser("resolve", ...)`, `p_resolve.add_argument("ref")`; dispatch in `_run_network` read branch: `data = await identity.resolve(tg, args.ref); return data, identity.to_rows(data)`.
- [ ] **Step 4: Gates, CONTRACT (`resolve` command + peer JSON shape), MAP.md row (`identity.py — tg resolve / contacts (peer discovery)`), commit.**

### Task 3: `tg contacts list|search`

**Files:**
- Modify: `src/tgcli/commands/identity.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_contacts.py`

**Interfaces:**
- Produces: `contacts_list(tg) -> {"contacts": [peer_dict, ...]}` via `contacts.getContacts` (users mapped through `peer_to_dict`). `contacts_search(tg, query, *, use_global=False)`: default filters `contacts_list` locally (case-insensitive substring over `display_name`/`username`); `use_global=True` calls `functions.contacts.SearchRequest(q=query, limit=...)` and maps `users`. Response gains `"scope": "local"|"global"`.
- CLI: `tg contacts list`, `tg contacts search QUERY [--global]` (sub-parser under a `contacts` group, mirroring `media`/`export`).

- [ ] **Step 1: Failing tests** in `tests/test_cli_contacts.py`:
  - `contacts list --json` → all contacts, each a peer dict.
  - `contacts search "iva"` (local) → only matching contacts; `scope == "local"`; assert the FakeClient made **no** global `SearchRequest`.
  - `contacts search "iva" --global` → FakeClient records a `contacts.SearchRequest`; `scope == "global"`.
- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_contacts.py -q`.
- [ ] **Step 3: Implement** the two functions + `contacts_to_rows`; wire the `contacts` sub-parser and dispatch. Reuse `peer_to_dict`.
- [ ] **Step 4: Gates, CONTRACT (`contacts` command, `scope` field, `--global`), commit.**

**End of slice 1:** all four gates, DEVLOG entry, merge/PR.

---

## Slice 2 — Inbox mutations (Tasks 4–5)

Both commands are direct (no preview), gated by `--readonly`, mirroring `mark-read`.

### Task 4: `tg mark-unread`

**Files:**
- Modify: `src/tgcli/commands/mutate.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_mutate.py`

**Interfaces:**
- Produces: `mark_unread(tg, chat) -> {"dialog": {"id"}, "marked_unread": True}` via `functions.messages.MarkDialogUnreadRequest(peer=..., unread=True)`. `to_rows` gains a `marked_unread` branch → `[(id, "unread")]`.
- CLI: `tg mark-unread CHAT` top-level, dispatch/gating cloned from the `mark-read` branch (`enforce_mutation_allowed`, mutation-safe client, audit record `mark-unread`).

- [ ] **Step 1: Failing tests**: `mark-unread @chan` records a `MarkDialogUnreadRequest` with `unread=True`; `--readonly` → exit 2; audit line `mark-unread` written.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** the function + `to_rows` branch; add the top-level parser and the dispatch/gate/audit lines next to `mark-read`.
- [ ] **Step 4: Gates, CONTRACT (`mark-unread` command), commit.**

### Task 5: `tg dialog pin|unpin`

**Files:**
- Create: `src/tgcli/commands/dialog.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`
- Test: `tests/test_cli_dialog.py`

**Interfaces:**
- Produces (module `dialog`): `set_pinned(tg, chat, pinned: bool) -> {"dialog": {"id"}, "pinned": bool}` via `functions.messages.ToggleDialogPinRequest(peer=..., pinned=pinned)`. `to_rows` → `[(id, "pinned"|"unpinned")]`.
- CLI: `dialog` sub-parser group with `pin CHAT` / `unpin CHAT`; both gated like `mark-read`; audit records `dialog-pin` / `dialog-unpin`.

- [ ] **Step 1: Failing tests**: `dialog pin @chan` → `ToggleDialogPinRequest(pinned=True)`; `dialog unpin @chan` → `pinned=False`; `--readonly` on either → exit 2; audit lines written.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** module + sub-parser + dispatch/gate/audit.
- [ ] **Step 4: Gates, CONTRACT (`dialog pin/unpin`), MAP.md row (`dialog.py — tg dialog pin/unpin (inbox state)`), commit.**

**End of slice 2:** all four gates, DEVLOG entry, merge/PR.

---

## Slice 3 — Discovery reads (Tasks 6–7)

### Task 6: `tg media manifest`

**Files:**
- Modify: `src/tgcli/commands/media.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_media.py`

**Interfaces:**
- Produces: `manifest(tg, source, *, kind=None, since=None, limit=100) -> {"dialog": {"id","name"}, "items": [{"message_id","type","size","mime","filename"}, ...], "count": int}`. Iterates `tg.iter_messages(entity, limit=limit)`, keeps only messages with media, classifies `type` from the Telethon message (`photo`/`video`/`audio`/`voice`/`document`), applies `--type` and `--since` filters, no download. Reuse `read._parse_when`-style ISO parsing already in `cli.py`.
- CLI: `media manifest SOURCE [--type ...] [--since ISO] [--limit N]` under the existing `media` sub-parser group.

- [ ] **Step 1: Failing tests** in `tests/test_cli_media.py`:
  - manifest over a chat of mixed media → `items` carry id/type/size/mime/filename; `count` matches; **no** download call on the FakeClient.
  - `--type video` → only video items.
  - `--since <iso>` → stops at the boundary (older items excluded).
  - `--type bogus` → argparse choices error, exit 2.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** `manifest` + a `_media_kind(message)` classifier + `to_rows`; wire the sub-parser (`--type` with `choices=[...]`) and dispatch (parse `--since` via the shared helper).
- [ ] **Step 4: Gates, CONTRACT (`media manifest` command + item shape), commit.**

### Task 7: `tg thread`

**Files:**
- Create: `src/tgcli/commands/thread.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`
- Test: `tests/test_cli_thread.py`

**Interfaces:**
- Produces (module `thread`): `fetch_thread(tg, chat, message_id, *, depth=20, want_replies=False, replies_limit=50) -> {"dialog": {...}, "root": msg, "ancestors": [msg...], "replies": [msg...], "note": str|None}`. `depth` is clamped to a hard cap of 100. Ancestors: from the root, repeatedly fetch the parent via `reply_to_msg_id` (`tg.get_messages(entity, ids=parent)`) up to `depth` steps, ordered oldest→newest, root excluded. Replies: only when `want_replies` and the root exposes a discussion/forum thread — call `messages.getReplies` (or `tg.get_messages(entity, reply_to=message_id)`), capped at `replies_limit`; otherwise `replies=[]` and `note="no cheap reply thread for this message; replies omitted"`. All messages rendered via `read.message_to_dict(msg, entity)`.
- CLI: `tg thread CHAT MESSAGE_ID [--replies] [--depth N] [--limit N]` (read).

- [ ] **Step 1: Failing tests** in `tests/test_cli_thread.py` (extend FakeClient `get_messages` to resolve single ids and, when `reply_to=` is passed, return the child list):
  - chain 1←2←3, `thread @chan 3` → `root.id == 3`, `ancestors` ids `[1, 2]`, `replies == []`.
  - `--depth 1` on the same chain → `ancestors == [2]` (bounded).
  - message with no `reply_to` → `ancestors == []`.
  - `thread @chan 3 --replies` where the client exposes a reply set → `replies` populated, `note is None`.
  - `--replies` where no thread exists → `replies == []`, `note` set.
  - `--depth 999` → clamped to 100 (assert no more than 100 parent fetches).
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** module + `to_rows` (root + ancestors + replies as message rows); wire parser and dispatch. Guard the ancestor walk against cycles (stop if a parent id repeats).
- [ ] **Step 4: Gates, CONTRACT (`thread` command + `{root,ancestors,replies,note}` shape), MAP.md row (`thread.py — tg thread reply-chain read`), commit.**

**End of slice 3:** all four gates, DEVLOG entry, merge/PR.

---

## Done criteria

- All seven tasks green across the four gates.
- CONTRACT.md documents every new command, flag, and JSON/TSV shape.
- MAP.md lists `identity.py`, `dialog.py`, `thread.py` and the `media`/`mutate` additions.
- ADR-0029 marked accepted; PROPOSALS.md items for these five moved out of the backlog.
- The five deferred-but-adjacent items (mutual-chats, bulk download, export incremental, batch, dialog archive/mute) remain in PROPOSALS.md untouched.
