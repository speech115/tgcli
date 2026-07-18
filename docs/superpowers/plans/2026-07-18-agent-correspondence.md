# Agent Correspondence (v1.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an AI agent read, triage, and conduct everyday Telegram correspondence through tgcli (ADR-0028).

**Architecture:** Three independently shippable slices, all contract-additive. Slice 1 enriches the read surface (message JSON, pagination). Slice 2 completes mutations under the existing preview→commit model, adding a `.pending` preview state and raw sends with `random_id` dedup. Slice 3 adds discovery flags and `tg doctor`.

**Tech Stack:** Python 3.12, Telethon, argparse, pytest. No new dependencies.

## Global Constraints

- ADR-0026/0028: this plan is the approved scope; nothing beyond it.
- Contract discipline (AGENTS.md): any task that changes CLI flags, JSON shapes, or TSV columns updates `docs/CONTRACT.md` **in the same commit**. All JSON changes here are additive (allowed without version bump per CONTRACT §3); TSV columns append at the end only.
- stdout is contract data only; everything else goes to stderr.
- TDD per task: failing test → minimal code → green → commit.
- Gates before every commit: `pytest -q`, `ruff check .`, `ruff format --check .`, `pyright` (run at least `pytest -q` per step; the full four at each task's commit step).
- Do NOT touch `src/tgcli/clone/` (frozen, live-accepted). The `random_id` confirmation helper is deliberately duplicated into `src/tgcli/confirm.py` (ADR-0028 Consequences).
- Each slice ends with a DEVLOG entry and is mergeable on its own.
- New commands follow existing layering: `commands/<name>.py` owns logic + `to_rows`; `cli.py` owns parsing, safety gating, and dispatch.

---

## Slice 1 — Read surface (Tasks 1–4)

### Task 1: Message JSON model extension

**Files:**
- Modify: `src/tgcli/commands/read.py`
- Modify: `src/tgcli/commands/search.py` (pass `entity` through)
- Modify: `docs/CONTRACT.md` (§5 message shape)
- Test: `tests/test_commands_read.py`

**Interfaces:**
- Produces: `message_to_dict(message, entity=None) -> dict` — existing 6 keys unchanged; new keys: `permalink`, `edited_at`, `outgoing`, `forwarded_from`, `reactions`, `topic_id`, `grouped_id`, `is_service`, `media_info`; `from` gains `username`. All later tasks reuse this exact shape.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_commands_read.py`:

```python
from datetime import UTC, datetime
from types import SimpleNamespace

from tgcli.commands.read import message_to_dict


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_message_to_dict_exposes_agent_fields():
    entity = _ns(id=1234, username="chan", broadcast=True)
    message = _ns(
        id=42,
        date=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        sender_id=111,
        sender=_ns(first_name="Alice", last_name=None, username="alice"),
        text="hello",
        media=None,
        reply_to_msg_id=None,
        edit_date=datetime(2026, 7, 18, 11, 0, tzinfo=UTC),
        out=True,
        forward=_ns(from_name="Bob", sender_id=222, chat_id=None,
                    date=datetime(2026, 7, 17, tzinfo=UTC)),
        reactions=_ns(results=[_ns(reaction=_ns(emoticon="👍"), count=3)]),
        grouped_id=777,
        action=None,
        file=None,
    )
    data = message_to_dict(message, entity)
    assert data["permalink"] == "https://t.me/chan/42"
    assert data["edited_at"] == "2026-07-18T11:00:00+00:00"
    assert data["outgoing"] is True
    assert data["from"] == {"id": 111, "name": "Alice", "username": "alice"}
    assert data["forwarded_from"] == {
        "name": "Bob", "id": 222, "date": "2026-07-17T00:00:00+00:00"}
    assert data["reactions"] == [{"emoji": "👍", "count": 3}]
    assert data["grouped_id"] == 777
    assert data["is_service"] is False
    assert data["media_info"] is None
    assert data["topic_id"] is None


def test_message_to_dict_media_topic_and_private_permalink():
    entity = _ns(id=999, username=None, megagroup=True)
    message = _ns(
        id=7, date=None, sender_id=1, sender=None, text="", media=_ns(),
        reply_to_msg_id=5,
        reply_to=_ns(forum_topic=True, reply_to_top_id=100, reply_to_msg_id=5),
        edit_date=None, out=False, forward=None, reactions=None,
        grouped_id=None, action=_ns(),
        file=_ns(name="doc.pdf", mime_type="application/pdf", size=100,
                 duration=None, width=None, height=None),
    )
    data = message_to_dict(message, entity)
    assert data["permalink"] == "https://t.me/c/999/7"
    assert data["topic_id"] == 100
    assert data["is_service"] is True
    assert data["media_info"] == {
        "name": "doc.pdf", "mime": "application/pdf", "size": 100,
        "duration": None, "width": None, "height": None}


def test_message_to_dict_minimal_namespace_still_works():
    # Old-style bare message (as other tests construct) must not crash.
    message = _ns(id=1, date=None, sender_id=None, sender=None, text=None,
                  media=None, reply_to_msg_id=None)
    data = message_to_dict(message)
    assert data["permalink"] is None
    assert data["media_info"] is None
    assert data["reactions"] == []
    assert data["outgoing"] is False
```

Note the third test: existing tests build messages as bare `SimpleNamespace` — every new field accessor must be `getattr`-safe.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands_read.py -q`
Expected: FAIL — `KeyError: 'permalink'` (and similar).

- [ ] **Step 3: Implement in `src/tgcli/commands/read.py`**

Add helpers above `message_to_dict` and extend it:

```python
def _permalink(entity, message_id: int) -> str | None:
    if entity is None:
        return None
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        return f"https://t.me/c/{entity.id}/{message_id}"
    return None


def _media_info(message) -> dict | None:
    file = getattr(message, "file", None)
    if file is None:
        return None
    return {
        "name": getattr(file, "name", None),
        "mime": getattr(file, "mime_type", None),
        "size": getattr(file, "size", None),
        "duration": getattr(file, "duration", None),
        "width": getattr(file, "width", None),
        "height": getattr(file, "height", None),
    }


def _reactions(message) -> list[dict]:
    results = getattr(getattr(message, "reactions", None), "results", None) or []
    out = []
    for item in results:
        reaction = getattr(item, "reaction", None)
        emoji = getattr(reaction, "emoticon", None)
        custom = getattr(reaction, "document_id", None)
        out.append(
            {"emoji": emoji or (str(custom) if custom else None), "count": item.count}
        )
    return out


def _forwarded_from(message) -> dict | None:
    forward = getattr(message, "forward", None)
    if forward is None:
        return None
    date = getattr(forward, "date", None)
    return {
        "name": getattr(forward, "from_name", None),
        "id": getattr(forward, "sender_id", None) or getattr(forward, "chat_id", None),
        "date": date.isoformat() if date else None,
    }


def _topic_id(message) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None or not getattr(reply, "forum_topic", False):
        return None
    return getattr(reply, "reply_to_top_id", None) or getattr(
        reply, "reply_to_msg_id", None
    )


def message_to_dict(message, entity=None) -> dict:
    edit_date = getattr(message, "edit_date", None)
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "from": {
            "id": message.sender_id,
            "name": _sender_name(message),
            "username": getattr(getattr(message, "sender", None), "username", None),
        },
        "text": message.text or "",
        "media": type(message.media).__name__ if message.media else None,
        "media_info": _media_info(message),
        "reply_to": message.reply_to_msg_id,
        "permalink": _permalink(entity, message.id),
        "edited_at": edit_date.isoformat() if edit_date else None,
        "outgoing": bool(getattr(message, "out", False)),
        "forwarded_from": _forwarded_from(message),
        "reactions": _reactions(message),
        "topic_id": _topic_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "is_service": getattr(message, "action", None) is not None,
    }
```

Pass `entity` at every call site in this file: `fetch_message` → `message_to_dict(message, entity)`; `fetch_messages` → `message_to_dict(message, entity)`. In `src/tgcli/commands/search.py`: `fetch_search` and `fetch_latest` → `message_to_dict(message, entity)`.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS (new tests green; `test_cli_read.py`/`test_cli_search.py` assert subsets or get the new keys — if any asserts full equality on a message dict, extend the expected dict with the new keys rather than weakening the assert).

- [ ] **Step 5: Update CONTRACT.md and commit**

In `docs/CONTRACT.md` §5, replace the `read` example message object with the full new shape (same values plus `"from": {"id": 111, "name": "Alice", "username": null}`, `"media_info": null, "permalink": null, "edited_at": null, "outgoing": false, "forwarded_from": null, "reactions": [], "topic_id": null, "grouped_id": null, "is_service": false`) and add one sentence: "All message-shape additions since 0.1 are additive; `media` remains the Telethon class name string, `media_info` carries structured metadata."

```bash
ruff check . && ruff format --check . && pyright && pytest -q
git add src/tgcli/commands/read.py src/tgcli/commands/search.py tests/test_commands_read.py docs/CONTRACT.md
git commit -m "Extend message JSON with agent-facing fields"
```

### Task 2: `read` pagination and `--topic`

**Files:**
- Modify: `src/tgcli/commands/read.py` (`fetch_messages`)
- Modify: `src/tgcli/cli.py` (flags + ISO date parsing)
- Modify: `tests/conftest.py` (`FakeClient.iter_messages` signature)
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_cli_read.py`

**Interfaces:**
- Consumes: `message_to_dict(message, entity)` from Task 1.
- Produces: `fetch_messages(tg, chat, limit=20, before_id=None, after_id=None, since=None, until=None, topic=None) -> dict`; response gains `"page": {"oldest_id": int|None, "newest_id": int|None}`. CLI flags `--before-id INT`, `--after-id INT`, `--since ISO`, `--until ISO`, `--topic INT`. Helper `_parse_when(parser, value, flag) -> datetime|None` in `cli.py` (reused by Task 4).

- [ ] **Step 1: Extend FakeClient** in `tests/conftest.py` — replace `iter_messages` with:

```python
    async def iter_messages(
        self,
        entity,
        search=None,
        limit=None,
        reverse=False,
        min_id=0,
        offset_id=0,
        offset_date=None,
        reply_to=None,
        from_user=None,
    ):
        self.iter_messages_calls.append((entity, search, limit))
        self.iter_messages_reverse_calls.append(reverse)
        self.iter_messages_kwargs = {
            "min_id": min_id, "offset_id": offset_id,
            "offset_date": offset_date, "reply_to": reply_to,
            "from_user": from_user,
        }
        if self.iter_messages_error is not None:
            raise self.iter_messages_error
        messages = self._search_messages.get(search, self._messages)
        if from_user is not None:
            wanted = str(from_user).lstrip("@")
            messages = [
                m for m in messages
                if getattr(getattr(m, "sender", None), "username", None) == wanted
            ]
        messages = [m for m in messages if m.id > min_id]
        if offset_id:
            messages = [m for m in messages if m.id < offset_id]
        if offset_date is not None:
            messages = [m for m in messages if m.date and m.date < offset_date]
        if reply_to is not None:
            messages = [m for m in messages if getattr(m, "topic", None) == reply_to]
        if reverse:
            messages = list(reversed(messages))
        for message in messages[:limit]:
            yield message
```

- [ ] **Step 2: Write the failing tests** in `tests/test_cli_read.py` (reuse that file's existing `config_env`/message fixtures; messages there are newest-first with descending ids):

```python
def test_read_before_and_after_id_map_to_offsets(config_env, monkeypatch, capsys):
    client = make_read_client()  # this file's existing helper for a 3-message chat
    make_session_fake(monkeypatch, client)
    assert main(["read", "@chan", "--before-id", "3", "--after-id", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in data["messages"]] == [2]
    assert client.iter_messages_kwargs["offset_id"] == 3
    assert client.iter_messages_kwargs["min_id"] == 1
    assert data["page"] == {"oldest_id": 2, "newest_id": 2}


def test_read_since_stops_at_boundary(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)
    assert main(["read", "@chan", "--since", "2026-07-18T00:00:00+00:00",
                 "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert all(m["date"] >= "2026-07-18" for m in data["messages"])


def test_read_topic_passes_reply_to(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)
    assert main(["read", "@chan", "--topic", "100", "--json"]) == 0
    assert client.iter_messages_kwargs["reply_to"] == 100


def test_read_rejects_bad_since(config_env, capsys):
    assert main(["read", "@chan", "--since", "yesterday"]) == 1
    assert "--since expects an ISO 8601" in capsys.readouterr().err
```

(If `test_cli_read.py` lacks a `make_read_client` helper, add one local to the test file building a `FakeClient` with three `ns(...)` messages with ids 3/2/1 and dates 2026-07-18/17/16, entity `{"@chan": ns(id=5, title="Chan")}`.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_cli_read.py -q` — Expected: FAIL (unknown flags).

- [ ] **Step 4: Implement**

`src/tgcli/commands/read.py`:

```python
async def fetch_messages(
    tg,
    chat: str,
    limit: int = 20,
    *,
    before_id: int | None = None,
    after_id: int | None = None,
    since=None,
    until=None,
    topic: int | None = None,
) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    messages = []
    async for message in tg.iter_messages(
        entity,
        limit=limit,
        offset_id=before_id or 0,
        min_id=after_id or 0,
        offset_date=until,
        reply_to=topic,
    ):
        if since is not None and message.date is not None and message.date < since:
            break
        messages.append(message_to_dict(message, entity))

    ids = [message["id"] for message in messages]
    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "messages": messages,
        "page": {
            "oldest_id": min(ids) if ids else None,
            "newest_id": max(ids) if ids else None,
        },
    }
```

`src/tgcli/cli.py` — flags on `p_read`:

```python
    p_read.add_argument("--before-id", type=int, help="only messages older than this id")
    p_read.add_argument("--after-id", type=int, help="only messages newer than this id")
    p_read.add_argument("--since", help="ISO date/datetime lower bound")
    p_read.add_argument("--until", help="ISO date/datetime upper bound")
    p_read.add_argument("--topic", type=int, help="forum topic id")
```

Module-level helper (near the top of `cli.py`, after imports; add `from datetime import UTC, datetime`):

```python
def _parse_when(parser: argparse.ArgumentParser, value: str | None, flag: str):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parser.error(f"{flag} expects an ISO 8601 date or datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
```

In `main()`, before the network run (inside the existing `try` that maps `SystemExit` from `parser.error` — follow the send-validation pattern of `try/except SystemExit: return 1`):

```python
        if args.command in ("read", "search"):
            try:
                args.since = _parse_when(parser, getattr(args, "since", None), "--since")
                args.until = _parse_when(parser, getattr(args, "until", None), "--until")
            except SystemExit:
                return 1
```

In `_run_network`, the read branch becomes:

```python
            if args.command == "read":
                data = await read_cmd.fetch_messages(
                    tg,
                    args.chat,
                    limit=args.limit,
                    before_id=args.before_id,
                    after_id=args.after_id,
                    since=args.since,
                    until=args.until,
                    topic=args.topic,
                )
                return data, read_cmd.to_rows(data)
```

(argparse with `argument_default=SUPPRESS` on globals does not apply here — these are per-command args and default to `None` automatically.)

- [ ] **Step 5: Run tests, update CONTRACT, commit**

Run: `pytest -q` then all four gates. In `docs/CONTRACT.md`: add the five flags to the `read` description, document `page` as additive, note `--since` output stays newest-first and stops at the boundary.

```bash
git add src/tgcli/commands/read.py src/tgcli/cli.py tests/conftest.py tests/test_cli_read.py docs/CONTRACT.md
git commit -m "Add read pagination, date bounds, and forum topic filter"
```

### Task 3: `message --context N`

**Files:**
- Modify: `src/tgcli/commands/read.py` (`fetch_message`), `src/tgcli/cli.py`, `tests/conftest.py` (`get_messages` ids-list), `docs/CONTRACT.md`
- Test: `tests/test_cli_message.py`

**Interfaces:**
- Produces: `fetch_message(tg, chat, message_id, context=0)`; with `context>0` the response gains `"context": [message...]` (neighbors only, target excluded, ascending id order). CLI flag `--context INT` (default 0).

- [ ] **Step 1: FakeClient** — in `tests/conftest.py` `get_messages`, support id lists:

```python
    async def get_messages(self, entity, ids=None, limit=None):
        self.get_messages_calls.append((entity, ids, limit))
        if limit == 0:
            return ns(total=self._message_total)
        if isinstance(ids, list):
            return [
                next((m for m in self._messages if m.id == i), None) for i in ids
            ]
        return next((message for message in self._messages if message.id == ids), None)
```

- [ ] **Step 2: Failing test** in `tests/test_cli_message.py` (reuse its fixtures/client):

```python
def test_message_context_returns_neighbors(config_env, monkeypatch, capsys):
    client = make_message_client()  # existing helper/fixture with messages 1..3
    make_session_fake(monkeypatch, client)
    assert main(["message", "@chan", "2", "--context", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["message"]["id"] == 2
    assert [m["id"] for m in data["context"]] == [1, 3]
```

- [ ] **Step 3: Run to verify failure** — `pytest tests/test_cli_message.py -q` → FAIL.

- [ ] **Step 4: Implement**

`fetch_message` in `read.py`:

```python
async def fetch_message(tg, chat: str, message_id: int, context: int = 0) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")

    data = {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "message": message_to_dict(message, entity),
    }
    if context > 0:
        ids = [
            i
            for i in range(message_id - context, message_id + context + 1)
            if i > 0 and i != message_id
        ]
        neighbors = await tg.get_messages(entity, ids=ids)
        data["context"] = [
            message_to_dict(item, entity) for item in neighbors if item is not None
        ]
    return data
```

`cli.py`: `p_message.add_argument("--context", type=int, default=0)`; dispatch passes `context=args.context`.

- [ ] **Step 5: Gates, CONTRACT (`message` gains optional additive `context` array), commit**

```bash
git add src/tgcli/commands/read.py src/tgcli/cli.py tests/conftest.py tests/test_cli_message.py docs/CONTRACT.md
git commit -m "Add message --context neighborhood read"
```

### Task 4: `search --from` / `--since`

**Files:**
- Modify: `src/tgcli/commands/search.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_search.py`

**Interfaces:**
- Consumes: FakeClient `from_user` filter (Task 2), `_parse_when` (Task 2).
- Produces: `fetch_search(tg, chat, query, limit=20, from_user=None, since=None)`. CLI: `--from @user`, `--since ISO` on `p_search`.

- [ ] **Step 1: Failing test** in `tests/test_cli_search.py`:

```python
def test_search_from_filters_by_sender(config_env, monkeypatch, capsys):
    client = make_search_client()  # existing helper; give one message sender username "alice"
    make_session_fake(monkeypatch, client)
    assert main(["search", "@chan", "hello", "--from", "@alice", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert client.iter_messages_kwargs["from_user"] == "@alice"
    assert all(m["from"]["username"] == "alice" for m in data["messages"])
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_search.py -q` → FAIL (unknown flag).

- [ ] **Step 3: Implement**

```python
async def fetch_search(
    tg, chat: str, query: str, limit: int = 20, *, from_user=None, since=None
) -> dict:
    entity = await _entity(tg, chat)
    messages = []
    async for message in tg.iter_messages(
        entity, search=query, limit=limit, from_user=from_user
    ):
        if since is not None and message.date is not None and message.date < since:
            break
        messages.append(message_to_dict(message, entity))
    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "query": query,
        "messages": messages,
    }
```

`cli.py`: `p_search.add_argument("--from", dest="from_user")`, `p_search.add_argument("--since")`; dispatch passes `from_user=args.from_user, since=args.since` (the `--since` parse from Task 2 already covers `search`).

- [ ] **Step 4: Gates, CONTRACT, commit**

```bash
git add src/tgcli/commands/search.py src/tgcli/cli.py tests/test_cli_search.py docs/CONTRACT.md
git commit -m "Add search sender and date filters"
```

**End of slice 1:** run all four gates, append DEVLOG entry, merge/PR.

---

## Slice 2 — Mutations (Tasks 5–10)

### Task 5: Two-phase preview commit in safety.py

**Files:**
- Modify: `src/tgcli/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Produces: `safety.begin_commit(preview_id, *, now=None) -> payload` (moves `.json` → `.pending`; a `.pending` preview may be re-begun — retry path), `safety.finish_commit(preview_id)` (moves `.pending` → `.used`). `consume_preview` is untouched (clone init keeps using it).

- [ ] **Step 1: Failing tests** in `tests/test_safety.py`:

```python
def test_begin_commit_allows_retry_until_finished():
    preview = safety.create_preview({"kind": "send", "text": "hi"})
    payload = safety.begin_commit(preview["preview_id"])
    assert payload["text"] == "hi"
    # network failed mid-send: begin again succeeds with the same payload
    assert safety.begin_commit(preview["preview_id"])["text"] == "hi"
    safety.finish_commit(preview["preview_id"])
    with pytest.raises(PolicyError):
        safety.begin_commit(preview["preview_id"])


def test_begin_commit_enforces_ttl_and_id_shape():
    preview = safety.create_preview({"kind": "send"})
    late = datetime.now(UTC) + timedelta(minutes=6)
    with pytest.raises(PolicyError):
        safety.begin_commit(preview["preview_id"], now=late)
    with pytest.raises(PolicyError):
        safety.begin_commit("p_missing")
    with pytest.raises(PolicyError):
        safety.begin_commit("../etc/passwd")
```

(Match this file's existing imports: `pytest`, `PolicyError`, `datetime`/`timedelta`.)

- [ ] **Step 2: Verify failure** — `pytest tests/test_safety.py -q` → FAIL (`AttributeError: begin_commit`).

- [ ] **Step 3: Implement** in `safety.py` (below `consume_preview`):

```python
def begin_commit(preview_id: str, *, now: datetime | None = None) -> dict:
    """Move a preview to .pending and return its payload.

    Unlike consume_preview, a .pending preview may be begun again: the
    stored random_id makes a retried network send idempotent (ADR-0028).
    """
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    path = previews_dir() / f"{preview_id}.json"
    pending = path.with_suffix(".pending")
    try:
        path.replace(pending)
    except FileNotFoundError:
        if not pending.exists():
            raise PolicyError("preview is already used or does not exist") from None
    record = json.loads(pending.read_text())
    now = now or datetime.now(UTC)
    if now >= datetime.fromisoformat(record["expires_at"]):
        raise PolicyError("preview has expired")
    return record["payload"]


def finish_commit(preview_id: str) -> None:
    pending = previews_dir() / f"{preview_id}.pending"
    try:
        pending.replace(pending.with_suffix(".used"))
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Gates and commit**

```bash
git add src/tgcli/safety.py tests/test_safety.py
git commit -m "Add two-phase preview commit with retryable pending state"
```

### Task 6: Shared random_id confirmation helper

**Files:**
- Create: `src/tgcli/confirm.py`
- Test: `tests/test_confirm.py`
- Modify: `docs/MAP.md` (new module row)

**Interfaces:**
- Produces: `confirmed_ids(response, random_ids: list[int]) -> list[int]` — raises `PolicyError("Telegram did not confirm the send")` unless every random_id maps to a unique positive message id. Same semantics as `clone.topics.confirmed_destination_ids` (deliberate duplication, ADR-0028 — do not import from or modify clone).

- [ ] **Step 1: Failing tests** in `tests/test_confirm.py`:

```python
import pytest
from telethon.tl import types

from tgcli.confirm import confirmed_ids
from tgcli.errors import PolicyError


def test_confirmed_ids_matches_random_ids():
    response = type("R", (), {
        "updates": [types.UpdateMessageID(id=42, random_id=7)]
    })()
    assert confirmed_ids(response, [7]) == [42]


def test_confirmed_ids_accepts_short_sent_message():
    response = types.UpdateShortSentMessage(
        id=42, pts=1, pts_count=1, date=None, out=True)
    assert confirmed_ids(response, [7]) == [42]


def test_confirmed_ids_fails_closed_on_missing_confirmation():
    response = type("R", (), {"updates": []})()
    with pytest.raises(PolicyError):
        confirmed_ids(response, [7])
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_confirm.py -q` → FAIL (no module).

- [ ] **Step 3: Implement** `src/tgcli/confirm.py`:

```python
"""Fail-closed message-id confirmation for random_id-based sends (ADR-0028)."""

from typing import cast

from telethon.tl import types

from tgcli.errors import PolicyError


def confirmed_ids(response, random_ids: list[int]) -> list[int]:
    if isinstance(response, types.UpdateShortSentMessage):
        message_id = response.id
        if (
            len(random_ids) == 1
            and isinstance(message_id, int)
            and not isinstance(message_id, bool)
            and message_id > 0
        ):
            return [message_id]
        raise PolicyError("Telegram did not confirm the send")
    updates = getattr(response, "updates", ())
    matches = {
        update.random_id: update.id
        for update in updates
        if isinstance(update, types.UpdateMessageID)
    }
    message_ids = [matches.get(random_id) for random_id in random_ids]
    if (
        set(matches) != set(random_ids)
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in message_ids
        )
        or len(set(message_ids)) != len(message_ids)
    ):
        raise PolicyError("Telegram did not confirm the send")
    return cast(list[int], message_ids)
```

- [ ] **Step 4: Gates, MAP.md row (`confirm.py — fail-closed random_id → message-id confirmation`), commit**

```bash
git add src/tgcli/confirm.py tests/test_confirm.py docs/MAP.md
git commit -m "Add shared random_id confirmation helper"
```

### Task 7: `send` prepare — reply/file/caption/topic/silent + random_id

**Files:**
- Modify: `src/tgcli/commands/send.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_send.py`

**Interfaces:**
- Produces: `prepare(tg, chat, text=None, *, reply_to=None, file=None, caption=None, topic=None, silent=False) -> dict`. Preview payload keys: `kind="send"`, `chat`, `text` (body or caption), `file` (absolute path or None), `file_size`, `reply_to`, `topic`, `silent`, `random_id`, `to`. CLI flags on `p_send`: `--reply-to INT`, `--file PATH`, `--caption TEXT`, `--topic INT`, `--silent`.

- [ ] **Step 1: Failing tests** in `tests/test_cli_send.py`:

```python
def test_send_preview_with_file_and_caption(config_env, monkeypatch, capsys, tmp_path):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"\xff\xd8fake")
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "@alice", "--file", str(photo),
                 "--caption", "look", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["file"] == str(photo)
    assert preview["file_size"] == 8
    assert preview["text"] == "look"

    stored = safety.begin_commit(preview["preview_id"])
    assert stored["kind"] == "send"
    assert stored["random_id"] > 0


def test_send_preview_records_reply_topic_silent(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "hi", "--reply-to", "5", "--topic", "9",
                 "--silent", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    stored = safety.begin_commit(preview["preview_id"])
    assert (stored["reply_to"], stored["topic"], stored["silent"]) == (5, 9, True)


def test_send_file_rejects_positional_text(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "hi", "--file", "x.jpg", "--preview"]) == 2


def test_send_missing_file_is_not_found(config_env, monkeypatch, capsys, tmp_path):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "--file", str(tmp_path / "nope.jpg"),
                 "--preview"]) == 4
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_send.py -q` → FAIL (unknown flags).

- [ ] **Step 3: Implement** `send.py` prepare side:

```python
"""Preview and replay the intentional Telegram send surface (ADR-0028)."""

import secrets
from pathlib import Path

from tgcli import chatref, safety
from tgcli.errors import NotFoundError, PolicyError


def _random_id() -> int:
    return secrets.randbelow(2**63 - 1) + 1


async def prepare(
    tg,
    chat: str,
    text: str | None = None,
    *,
    reply_to: int | None = None,
    file: str | None = None,
    caption: str | None = None,
    topic: int | None = None,
    silent: bool = False,
) -> dict:
    path = None
    if file is not None:
        if text is not None:
            raise PolicyError("send --file takes --caption, not positional text")
        path = Path(file).expanduser()
        if not path.is_file():
            raise NotFoundError(f"file not found: {file}")
        body = caption or ""
    else:
        if caption is not None:
            raise PolicyError("send --caption requires --file")
        if text is None:
            raise PolicyError("send requires TEXT or --file")
        body = text

    entity = await tg.get_entity(chatref.parse(chat))
    stored = safety.create_preview(
        {
            "kind": "send",
            "chat": chat,
            "text": body,
            "file": str(path) if path else None,
            "file_size": path.stat().st_size if path else None,
            "reply_to": reply_to,
            "topic": topic,
            "silent": silent,
            "random_id": _random_id(),
            "to": _target_to_dict(entity),
        }
    )
    keys = (
        "preview_id", "to", "text", "file", "file_size",
        "reply_to", "topic", "silent", "expires_at",
    )
    return {key: stored[key] for key in keys}
```

Keep `_target_to_dict` as is. Update `to_rows` preview row to append `file` and `reply_to` at the end of the tuple (TSV columns append-only).

`cli.py` parser:

```python
    p_send.add_argument("--reply-to", type=int, dest="reply_to")
    p_send.add_argument("--file")
    p_send.add_argument("--caption")
    p_send.add_argument("--topic", type=int)
    p_send.add_argument("--silent", action="store_true")
```

`main()` send validation — the preview branch condition becomes “chat and (text or --file)”:

```python
            elif not (
                args.preview
                and args.chat is not None
                and (args.text is not None or args.file is not None)
            ):
```

`_run_network` send-preview call:

```python
                    data = await send_cmd.prepare(
                        tg,
                        args.chat,
                        args.text,
                        reply_to=args.reply_to,
                        file=args.file,
                        caption=args.caption,
                        topic=args.topic,
                        silent=args.silent,
                    )
```

Existing `test_send_preview_persists_payload_without_sending` asserts the full stored payload — extend its expected dict with the new keys (`kind`, `file: None`, `file_size: None`, `reply_to: None`, `topic: None`, `silent: False`, `random_id: <assert isinstance int>`); switch its `consume_preview` call to `begin_commit`.

- [ ] **Step 4: Gates, CONTRACT (send flags + preview JSON shape), commit**

```bash
git add src/tgcli/commands/send.py src/tgcli/cli.py tests/test_cli_send.py docs/CONTRACT.md
git commit -m "Extend send preview with reply, file, topic, silent, random_id"
```

### Task 8: `send` commit — raw requests, retryable, result audit

**Files:**
- Modify: `src/tgcli/commands/send.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_send.py`

**Interfaces:**
- Consumes: `safety.begin_commit`/`finish_commit` (Task 5), `confirm.confirmed_ids` (Task 6), Task 7 payload.
- Produces: `commit(tg, preview_id, payload) -> {"preview_id", "message_id"}` via `SendMessageRequest`/`SendMediaRequest` with the stored `random_id`. `cli.py`: send commit uses `begin_commit`, then on success `finish_commit` + audit record `send-result {preview_id, message_id}`; the pre-send audit record gains `random_id`.

- [ ] **Step 1: Failing tests** — replace `SendClient` in `tests/test_cli_send.py` with a raw-request recorder and update commit tests:

```python
from telethon.tl import types


class SendClient:
    def __init__(self):
        self.requests = []
        self.uploaded = []

    async def get_entity(self, chat):
        assert chat == "@alice"
        return SimpleNamespace(id=7, title="Alice")

    async def get_input_entity(self, chat):
        return f"input:{chat}"

    async def upload_file(self, path):
        self.uploaded.append(path)
        return SimpleNamespace(name=path)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=42, random_id=request.random_id)]
        )


def test_send_commit_sends_raw_with_stored_random_id(config_env, monkeypatch, capsys):
    preview = safety.create_preview({
        "kind": "send", "chat": "@alice", "text": "hello", "file": None,
        "file_size": None, "reply_to": 5, "topic": None, "silent": True,
        "random_id": 777, "to": {"id": 7, "name": "Alice"},
    })
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"], "message_id": 42}
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.random_id == 777
    assert request.silent is True
    assert request.reply_to.reply_to_msg_id == 5
    # finished: a second commit is refused
    assert main(["send", "--commit", preview["preview_id"]]) == 2
    # result audit record exists
    lines = [json.loads(line) for line in
             safety.audit_path().read_text().splitlines()]
    assert lines[-1]["action"] == "send-result"
    assert lines[-1]["message_id"] == 42


def test_send_commit_is_retryable_after_network_failure(config_env, monkeypatch, capsys):
    preview = safety.create_preview({
        "kind": "send", "chat": "@alice", "text": "hello", "file": None,
        "file_size": None, "reply_to": None, "topic": None, "silent": False,
        "random_id": 778, "to": {"id": 7, "name": "Alice"},
    })
    client = SendClient()

    async def boom(request):
        raise OSError("connection reset")

    client.__call__ = boom  # type: ignore[method-assign]
    failing = type("F", (SendClient,), {"__call__": staticmethod(boom)})
    make_session_fake(monkeypatch, client)
    monkeypatch.setattr(client, "__call__", boom, raising=False)

    with pytest.raises(OSError):
        main(["send", "--commit", preview["preview_id"], "--json"])

    # the preview is pending, not burned: commit again with a working client
    working = SendClient()
    make_session_fake(monkeypatch, working)
    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert working.requests[0].random_id == 778
```

(Note: patching `__call__` on an instance does not affect `client(request)` — bind the failing behavior by subclassing instead: `class FailingClient(SendClient): async def __call__(self, request): raise OSError(...)`. Use that; delete the monkeypatch lines above. Add `from telethon.tl import functions` to imports. Unhandled exceptions propagate out of `main` — the `pytest.raises(OSError)` reflects the current cli contract for non-Tgcli errors.)

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_send.py -q` → FAIL.

- [ ] **Step 3: Implement**

`send.py` commit side:

```python
import mimetypes

from telethon.tl import functions, types

from tgcli.confirm import confirmed_ids


def _reply_header(payload: dict):
    reply_to, topic = payload.get("reply_to"), payload.get("topic")
    if reply_to is None and topic is None:
        return None
    return types.InputReplyToMessage(
        reply_to_msg_id=reply_to if reply_to is not None else topic,
        top_msg_id=topic if (reply_to is not None and topic is not None) else None,
    )


def _uploaded_media(uploaded, file: str):
    mime = mimetypes.guess_type(file)[0] or "application/octet-stream"
    if mime in ("image/jpeg", "image/png"):
        return types.InputMediaUploadedPhoto(file=uploaded)
    return types.InputMediaUploadedDocument(
        file=uploaded,
        mime_type=mime,
        attributes=[types.DocumentAttributeFilename(Path(file).name)],
    )


async def commit(tg, preview_id: str, payload: dict) -> dict:
    peer = await tg.get_input_entity(chatref.parse(payload["chat"]))
    random_id = payload["random_id"]
    silent = payload.get("silent") or None
    reply_to = _reply_header(payload)
    if payload.get("file"):
        uploaded = await tg.upload_file(payload["file"])
        request = functions.messages.SendMediaRequest(
            peer=peer,
            media=_uploaded_media(uploaded, payload["file"]),
            message=payload["text"],
            random_id=random_id,
            silent=silent,
            reply_to=reply_to,
        )
    else:
        request = functions.messages.SendMessageRequest(
            peer=peer,
            message=payload["text"],
            random_id=random_id,
            silent=silent,
            reply_to=reply_to,
        )
    response = await tg(request)
    [message_id] = confirmed_ids(response, [random_id])
    return {"preview_id": preview_id, "message_id": message_id}
```

`cli.py` changes:

1. Send commit loads via `begin_commit` and checks kind:

```python
                safety.enforce_mutation_allowed(args.readonly)
                args.preview_payload = safety.begin_commit(args.commit)
                if args.preview_payload.get("kind") != "send":
                    raise PolicyError("preview does not match send")
```

2. Pre-send audit gains random_id:

```python
                if args.command == "send" and args.commit:
                    safety.append_audit(
                        "send",
                        account.alias,
                        {
                            "preview_id": args.commit,
                            "random_id": args.preview_payload.get("random_id"),
                        },
                    )
```

3. After the `asyncio.run(...)` that produced `data` (both timeout branches), finish and record the result:

```python
                if getattr(args, "commit", None) and args.command == "send":
                    safety.finish_commit(args.commit)
                    safety.append_audit(
                        "send-result",
                        account.alias,
                        {"preview_id": args.commit, "message_id": data.get("message_id")},
                    )
```

Old commit tests (`test_send_commit_replays_stored_payload_once`, readonly-block test) need their manually built previews extended with the Task 7 payload keys (`kind`, `random_id`, ...).

- [ ] **Step 4: Gates, CONTRACT (retry semantics: "a failed commit may be re-committed; Telegram deduplicates by random_id within the preview TTL"), commit**

```bash
git add src/tgcli/commands/send.py src/tgcli/cli.py tests/test_cli_send.py docs/CONTRACT.md
git commit -m "Send commit via raw request with retryable random_id"
```

### Task 9: `edit` and `delete`

**Files:**
- Create: `src/tgcli/commands/mutate.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`
- Test: `tests/test_cli_mutate.py`

**Interfaces:**
- Produces (module `mutate`): `prepare_edit(tg, chat, message_id, text)`, `commit_edit(tg, preview_id, payload)`, `prepare_delete(tg, chat, message_id)`, `commit_delete(tg, preview_id, payload)`, `to_rows(data)`. Preview kinds `"edit"`/`"delete"`; edit preview shows `old_text` beside `text`; delete preview shows the target's `text`. Commits are naturally idempotent (no random_id needed).
- CLI: `tg edit CHAT ID TEXT --preview | --commit P`, `tg delete CHAT ID --preview | --commit P`; validation/gating mirrors send (enforce → begin_commit → kind check → audit → run → finish_commit → result audit).

- [ ] **Step 1: Failing tests** — `tests/test_cli_mutate.py`:

```python
import json
from types import SimpleNamespace

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import safety
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class MutateClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.edited = []
        self.deleted = []

    async def edit_message(self, chat, message_id, text):
        self.edited.append((chat, message_id, text))
        return ns(id=message_id)

    async def delete_messages(self, chat, ids, revoke=True):
        self.deleted.append((chat, ids, revoke))


def make_client():
    message = ns(id=2, date=None, sender_id=1, sender=None, text="old",
                 media=None, reply_to_msg_id=None)
    return MutateClient(messages=[message], entities={"@chan": ns(id=5, title="Chan")})


def test_edit_preview_shows_old_and_new_text(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["edit", "@chan", "2", "new", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["old_text"], preview["text"]) == ("old", "new")
    assert client.edited == []


def test_edit_commit_edits_and_audits(config_env, monkeypatch, capsys):
    preview = safety.create_preview({
        "kind": "edit", "chat": "@chan", "message_id": 2,
        "old_text": "old", "text": "new"})
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["edit", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.edited == [("@chan", 2, "new")]
    assert main(["edit", "--commit", preview["preview_id"]]) == 2


def test_delete_commit_revokes(config_env, monkeypatch, capsys):
    preview = safety.create_preview({
        "kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"})
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["delete", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.deleted == [("@chan", [2], True)]


def test_kind_mismatch_is_blocked(config_env, monkeypatch):
    preview = safety.create_preview({
        "kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"})
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["edit", "--commit", preview["preview_id"]]) == 2
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_mutate.py -q` → FAIL (unknown command).

- [ ] **Step 3: Implement**

`src/tgcli/commands/mutate.py`:

```python
"""Preview→commit mutations on existing messages (ADR-0028)."""

import secrets

from telethon.tl import functions

from tgcli import chatref, safety
from tgcli.commands.read import sanitize_plain_text
from tgcli.confirm import confirmed_ids
from tgcli.errors import NotFoundError


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def _message(tg, entity, message_id: int):
    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")
    return message


def _random_id() -> int:
    return secrets.randbelow(2**63 - 1) + 1


async def prepare_edit(tg, chat: str, message_id: int, text: str) -> dict:
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    stored = safety.create_preview(
        {
            "kind": "edit",
            "chat": chat,
            "message_id": message_id,
            "old_text": message.text or "",
            "text": text,
        }
    )
    keys = ("preview_id", "message_id", "old_text", "text", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_edit(tg, preview_id: str, payload: dict) -> dict:
    message = await tg.edit_message(
        payload["chat"], payload["message_id"], payload["text"]
    )
    return {"preview_id": preview_id, "message_id": message.id}


async def prepare_delete(tg, chat: str, message_id: int) -> dict:
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    stored = safety.create_preview(
        {
            "kind": "delete",
            "chat": chat,
            "message_id": message_id,
            "text": message.text or "",
        }
    )
    keys = ("preview_id", "message_id", "text", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_delete(tg, preview_id: str, payload: dict) -> dict:
    await tg.delete_messages(payload["chat"], [payload["message_id"]], revoke=True)
    return {"preview_id": preview_id, "message_id": payload["message_id"]}


def to_rows(data: dict) -> list[tuple]:
    if "old_text" in data:
        return [(data["preview_id"], data["message_id"],
                 sanitize_plain_text(data["old_text"]),
                 sanitize_plain_text(data["text"]))]
    if "text" in data:
        return [(data["preview_id"], data["message_id"],
                 sanitize_plain_text(data["text"]))]
    return [(data["preview_id"], data["message_id"])]
```

`cli.py` — parsers:

```python
    p_edit = sub.add_parser(
        "edit", help="Preview and commit a message edit", parents=[global_flags]
    )
    p_edit.add_argument("chat", nargs="?")
    p_edit.add_argument("message_id", nargs="?", type=int)
    p_edit.add_argument("text", nargs="?")
    p_edit.add_argument("--preview", action="store_true")
    p_edit.add_argument("--commit", metavar="PREVIEW_ID")

    p_delete = sub.add_parser(
        "delete", help="Preview and commit a message deletion", parents=[global_flags]
    )
    p_delete.add_argument("chat", nargs="?")
    p_delete.add_argument("message_id", nargs="?", type=int)
    p_delete.add_argument("--preview", action="store_true")
    p_delete.add_argument("--commit", metavar="PREVIEW_ID")
```

Generalize the send validation block in `main()` — replace the send-only block with a table-driven one (send keeps its special text-or-file rule):

```python
        MUTATION_POSITIONALS = {
            "edit": ("chat", "message_id", "text"),
            "delete": ("chat", "message_id"),
            "forward": ("source", "message_id", "destination"),
        }
        if args.command in MUTATION_POSITIONALS:
            names = MUTATION_POSITIONALS[args.command]
            values = [getattr(args, name) for name in names]
            if args.commit:
                if args.preview or any(value is not None for value in values):
                    try:
                        parser.error(
                            f"{args.command} --commit accepts only a preview id"
                        )
                    except SystemExit:
                        return 1
                safety.enforce_mutation_allowed(args.readonly)
                args.preview_payload = safety.begin_commit(args.commit)
                if args.preview_payload.get("kind") != args.command:
                    raise PolicyError(f"preview does not match {args.command}")
            elif not (args.preview and all(value is not None for value in values)):
                try:
                    parser.error(
                        f"{args.command} requires "
                        f"{' '.join(name.upper() for name in names)} --preview "
                        "or --commit PREVIEW_ID"
                    )
                except SystemExit:
                    return 1
```

(`forward` enters this table in Task 10 — including it now is harmless because the parser for it does not exist yet.)

Audit + finish generalize too — replace the send-specific audit/finish lines from Task 8:

```python
                if args.command in ("send", "edit", "delete", "forward") and getattr(
                    args, "commit", None
                ):
                    details = {"preview_id": args.commit}
                    if "random_id" in args.preview_payload:
                        details["random_id"] = args.preview_payload["random_id"]
                    safety.append_audit(args.command, account.alias, details)
```

and after `data` is produced:

```python
                if args.command in ("send", "edit", "delete", "forward") and getattr(
                    args, "commit", None
                ):
                    safety.finish_commit(args.commit)
                    safety.append_audit(
                        f"{args.command}-result",
                        account.alias,
                        {"preview_id": args.commit, "message_id": data.get("message_id")},
                    )
```

`_run_network` dispatch:

```python
            if args.command == "edit":
                if args.preview:
                    data = await mutate_cmd.prepare_edit(
                        tg, args.chat, args.message_id, args.text
                    )
                else:
                    data = await mutate_cmd.commit_edit(
                        tg, args.commit, args.preview_payload
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "delete":
                if args.preview:
                    data = await mutate_cmd.prepare_delete(
                        tg, args.chat, args.message_id
                    )
                else:
                    data = await mutate_cmd.commit_delete(
                        tg, args.commit, args.preview_payload
                    )
                return data, mutate_cmd.to_rows(data)
```

Add `from tgcli.commands import mutate as mutate_cmd` to imports.

- [ ] **Step 4: Gates, CONTRACT (new commands, JSON/TSV shapes, exit-code semantics unchanged), MAP.md (`commands/mutate.py`), commit**

```bash
git add src/tgcli/commands/mutate.py src/tgcli/cli.py tests/test_cli_mutate.py docs/CONTRACT.md docs/MAP.md
git commit -m "Add edit and delete under preview-commit"
```

### Task 10: `forward` and `mark-read`

**Files:**
- Modify: `src/tgcli/commands/mutate.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_mutate.py`

**Interfaces:**
- Consumes: Task 9 gating table (already contains `forward`), `confirmed_ids`.
- Produces: `prepare_forward(tg, source, message_id, destination)` (payload kind `"forward"`, includes `random_id`), `commit_forward(tg, preview_id, payload)` via `ForwardMessagesRequest`; `mark_read(tg, chat) -> {"dialog": {...}, "marked_read": true}` — no preview, but `safety.enforce_mutation_allowed` + audit `mark-read {chat}` in `cli.py`.

- [ ] **Step 1: Failing tests** in `tests/test_cli_mutate.py`:

```python
def test_forward_commit_uses_stored_random_id(config_env, monkeypatch, capsys):
    preview = safety.create_preview({
        "kind": "forward", "source": "@chan", "message_id": 2,
        "destination": "@other", "text": "old", "random_id": 555})
    client = make_client()
    client._entities["@other"] = ns(id=6, title="Other")

    from telethon.tl import types

    async def call(request):
        client.forwarded = request
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=99, random_id=555)])

    client.__class__.__call__ = staticmethod(call)  # NO — use subclass below
    make_session_fake(monkeypatch, client)
    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["message_id"] == 99
    assert client.forwarded.random_id == [555]
    assert client.forwarded.id == [2]


def test_mark_read_needs_no_preview_but_respects_readonly(config_env, monkeypatch, capsys):
    client = make_client()

    async def ack(entity):
        client.acked = entity

    client.send_read_acknowledge = ack
    make_session_fake(monkeypatch, client)
    assert main(["mark-read", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_read"] is True
    assert main(["--readonly", "mark-read", "@chan"]) == 2
```

(For the forward client, define a proper subclass in the test file instead of patching `__call__` at class level:

```python
class ForwardClient(MutateClient):
    async def __call__(self, request):
        from telethon.tl import types
        self.forwarded = request
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=99, random_id=request.random_id[0])])
```

and use `ForwardClient(...)` in the test.)

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_mutate.py -q` → FAIL.

- [ ] **Step 3: Implement**

`mutate.py` additions:

```python
async def prepare_forward(
    tg, source: str, message_id: int, destination: str
) -> dict:
    src = await _entity(tg, source)
    message = await _message(tg, src, message_id)
    await _entity(tg, destination)  # fail fast if unknown
    stored = safety.create_preview(
        {
            "kind": "forward",
            "source": source,
            "message_id": message_id,
            "destination": destination,
            "text": message.text or "",
            "random_id": _random_id(),
        }
    )
    keys = (
        "preview_id", "source", "message_id", "destination", "text", "expires_at",
    )
    return {key: stored[key] for key in keys}


async def commit_forward(tg, preview_id: str, payload: dict) -> dict:
    response = await tg(
        functions.messages.ForwardMessagesRequest(
            from_peer=await tg.get_input_entity(chatref.parse(payload["source"])),
            id=[payload["message_id"]],
            random_id=[payload["random_id"]],
            to_peer=await tg.get_input_entity(chatref.parse(payload["destination"])),
        )
    )
    [message_id] = confirmed_ids(response, [payload["random_id"]])
    return {"preview_id": preview_id, "message_id": message_id}


async def mark_read(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    await tg.send_read_acknowledge(entity)
    return {"dialog": {"id": entity.id}, "marked_read": True}
```

Extend `to_rows` first branch guard so forward previews render (`"destination" in data` → row `(preview_id, source, message_id, destination)` appended columns). `mark_read` row: `[(data["dialog"]["id"], "read")]`.

`cli.py` parsers:

```python
    p_forward = sub.add_parser(
        "forward", help="Preview and commit a forward", parents=[global_flags]
    )
    p_forward.add_argument("source", nargs="?")
    p_forward.add_argument("message_id", nargs="?", type=int)
    p_forward.add_argument("destination", nargs="?")
    p_forward.add_argument("--preview", action="store_true")
    p_forward.add_argument("--commit", metavar="PREVIEW_ID")

    p_mark_read = sub.add_parser(
        "mark-read", help="Mark a dialog as read", parents=[global_flags]
    )
    p_mark_read.add_argument("chat")
```

`main()` gating for mark-read (argparse turns `mark-read` into `args.command == "mark-read"`):

```python
        if args.command == "mark-read":
            safety.enforce_mutation_allowed(args.readonly)
```

and audit next to the other audits: `safety.append_audit("mark-read", account.alias, {"chat": args.chat})`.

`_run_network` dispatch:

```python
            if args.command == "forward":
                if args.preview:
                    data = await mutate_cmd.prepare_forward(
                        tg, args.source, args.message_id, args.destination
                    )
                else:
                    data = await mutate_cmd.commit_forward(
                        tg, args.commit, args.preview_payload
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "mark-read":
                data = await mutate_cmd.mark_read(tg, args.chat)
                return data, mutate_cmd.to_rows(data)
```

- [ ] **Step 4: Gates, CONTRACT, commit**

```bash
git add src/tgcli/commands/mutate.py src/tgcli/cli.py tests/test_cli_mutate.py docs/CONTRACT.md
git commit -m "Add forward and mark-read"
```

**End of slice 2:** four gates, DEVLOG entry, merge/PR. Then a live smoke test with the owner: `tg send` a file with caption + reply into a private test chat, `tg edit`, `tg delete`, `tg forward` — visual acceptance per owner preference.

---

## Slice 3 — Discovery and ops (Tasks 11–15)

### Task 11: `dialogs --unread-only --kind` + `mentions`

**Files:**
- Modify: `src/tgcli/commands/dialogs.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_dialogs.py`

**Interfaces:**
- Produces: `fetch_dialogs(tg, limit=50, unread_only=False, kind=None)`; dialog dict gains `"mentions"`. CLI: `--unread-only`, `--kind {user,group,channel}`.

- [ ] **Step 1: Failing test** in `tests/test_cli_dialogs.py` (reuse its dialog fixtures; give one dialog `unread_count=0`):

```python
def test_dialogs_unread_only_and_kind_filter(config_env, monkeypatch, capsys):
    client = make_dialogs_client()  # existing helper: ensure ≥1 read dialog, ≥1 unread user dialog
    make_session_fake(monkeypatch, client)
    assert main(["dialogs", "--unread-only", "--kind", "user", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"]
    assert all(d["kind"] == "user" and d["unread"] > 0 for d in data["dialogs"])
    assert all("mentions" in d for d in data["dialogs"])
```

- [ ] **Step 2: Verify failure**, then **Step 3: Implement**:

```python
async def fetch_dialogs(
    tg, limit: int = 50, *, unread_only: bool = False, kind: str | None = None
) -> dict:
    dialogs = []
    async for dialog in tg.iter_dialogs():
        mentions = getattr(
            getattr(dialog, "dialog", None), "unread_mentions_count", 0
        ) or 0
        if unread_only and not (dialog.unread_count or mentions):
            continue
        if kind is not None and _kind(dialog) != kind:
            continue
        dialogs.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "kind": _kind(dialog),
                "username": getattr(dialog.entity, "username", None),
                "unread": dialog.unread_count,
                "mentions": mentions,
                "last_message_at": dialog.date.isoformat() if dialog.date else None,
            }
        )
        if len(dialogs) >= limit:
            break
    return {"dialogs": dialogs}
```

`to_rows`: append `dialog["mentions"]` as the last column. `cli.py`: `p_dialogs.add_argument("--unread-only", action="store_true")`, `p_dialogs.add_argument("--kind", choices=["user", "group", "channel"])`; dispatch passes both. `FakeClient.iter_dialogs` is already compatible (called without `limit`).

- [ ] **Step 4: Gates, CONTRACT (dialog shape + flags; TSV column appended), commit**

```bash
git add src/tgcli/commands/dialogs.py src/tgcli/cli.py tests/test_cli_dialogs.py docs/CONTRACT.md
git commit -m "Add dialogs unread/kind filters and mentions count"
```

### Task 12: `search --all` (global search)

**Files:**
- Modify: `src/tgcli/commands/search.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_search.py`

**Interfaces:**
- Produces: `fetch_search_all(tg, query, limit=20) -> {"query", "messages": [message + "dialog": {"id", "name"}]}`. CLI: `tg search --all QUERY` (`chat`/`query` positionals become `nargs="?"`; with `--all` the single positional is the query).

- [ ] **Step 1: Failing test**:

```python
def test_search_all_returns_per_hit_dialogs(config_env, monkeypatch, capsys):
    message = ns(id=1, date=None, sender_id=1, sender=None, text="invoice",
                 media=None, reply_to_msg_id=None,
                 chat=ns(id=5, title="Chan"), chat_id=5)
    client = FakeClient(search_messages={"invoice": [message]})
    make_session_fake(monkeypatch, client)
    assert main(["search", "--all", "invoice", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["query"] == "invoice"
    assert data["messages"][0]["dialog"] == {"id": 5, "name": "Chan"}
    assert client.iter_messages_calls[0][0] is None


def test_search_all_rejects_extra_positional(config_env, capsys):
    assert main(["search", "--all", "@chan", "invoice"]) == 1
    assert "search --all takes exactly one QUERY" in capsys.readouterr().err
```

- [ ] **Step 2: Verify failure**, then **Step 3: Implement**

`search.py`:

```python
async def fetch_search_all(tg, query: str, limit: int = 20) -> dict:
    messages = []
    async for message in tg.iter_messages(None, search=query, limit=limit):
        chat = getattr(message, "chat", None)
        entry = message_to_dict(message, chat)
        entry["dialog"] = {
            "id": getattr(message, "chat_id", None),
            "name": _dialog_name(chat, "") if chat is not None else None,
        }
        messages.append(entry)
    return {"query": query, "messages": messages}
```

`cli.py`: `p_search` positionals become `p_search.add_argument("chat", nargs="?")` and `p_search.add_argument("query", nargs="?")`; add `p_search.add_argument("--all", action="store_true")`. Validation in `main()` (same `try/except SystemExit: return 1` pattern):

```python
        if args.command == "search":
            if args.all:
                if args.query is not None or args.chat is None:
                    parser.error("search --all takes exactly one QUERY")
                args.query, args.chat = args.chat, None
            elif args.chat is None or args.query is None:
                parser.error("search requires CHAT QUERY (or --all QUERY)")
```

Dispatch: `if args.all: data = await search_cmd.fetch_search_all(tg, args.query, limit=args.limit)` else the existing per-chat call.

- [ ] **Step 4: Gates, CONTRACT, commit**

```bash
git add src/tgcli/commands/search.py src/tgcli/cli.py tests/test_cli_search.py docs/CONTRACT.md
git commit -m "Add global search"
```

### Task 13: `info --full`

**Files:**
- Modify: `src/tgcli/commands/info.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`
- Test: `tests/test_cli_info.py`

**Interfaces:**
- Produces: `fetch_info_full(tg, chat)` = base info + `role` (`"creator"|"admin"|"member"|None` for user dialogs), `can` map (best-effort booleans or None), `slowmode_seconds`, `participants_count`, `about`. Uses `GetFullChannelRequest` only for channel/megagroup entities.

- [ ] **Step 1: Failing test** in `tests/test_cli_info.py`:

```python
def test_info_full_reports_role_rights_and_slowmode(config_env, monkeypatch, capsys):
    entity = ns(id=5, title="Chan", username="chan", broadcast=False,
                megagroup=True, creator=False,
                admin_rights=ns(delete_messages=True, pin_messages=True,
                                edit_messages=False),
                banned_rights=None, default_banned_rights=None)
    client = FakeClient(entities={"@chan": entity})

    class FullClient(type(client)):
        async def __call__(self, request):
            return ns(full_chat=ns(slowmode_seconds=30, participants_count=12,
                                   about="rules"))

    client.__class__ = FullClient
    make_session_fake(monkeypatch, client)
    assert main(["info", "@chan", "--full", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["role"] == "admin"
    assert data["can"]["send_messages"] is True
    assert data["can"]["delete_messages"] is True
    assert data["slowmode_seconds"] == 30
    assert data["participants_count"] == 12
    assert data["about"] == "rules"
```

(If mutating `__class__` proves awkward, define a small `FullClient(FakeClient)` subclass with the `__call__` and build it directly.)

- [ ] **Step 2: Verify failure**, then **Step 3: Implement** in `info.py`:

```python
from telethon.tl import functions


def _role(entity) -> str | None:
    if _kind(entity) == "user":
        return None
    if getattr(entity, "creator", False):
        return "creator"
    if getattr(entity, "admin_rights", None) is not None:
        return "admin"
    return "member"


def _can(entity, flag: str) -> bool | None:
    if _kind(entity) == "user":
        return True
    if getattr(entity, "creator", False) or getattr(entity, "admin_rights", None):
        return True
    if getattr(entity, "broadcast", False):
        return False  # plain member of a broadcast channel cannot post
    banned = getattr(entity, "banned_rights", None) or getattr(
        entity, "default_banned_rights", None
    )
    if banned is None:
        return None  # rights unknown; best-effort contract
    return not getattr(banned, flag, False)


async def fetch_info_full(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    base = await fetch_info(tg, chat)
    full = None
    if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        response = await tg(functions.channels.GetFullChannelRequest(channel=entity))
        full = response.full_chat
    admin = getattr(entity, "admin_rights", None)
    return {
        **base,
        "role": _role(entity),
        "can": {
            "send_messages": _can(entity, "send_messages"),
            "send_media": _can(entity, "send_media"),
            "pin_messages": bool(getattr(admin, "pin_messages", False))
            or _can(entity, "pin_messages"),
            "delete_messages": bool(getattr(admin, "delete_messages", False))
            or _kind(entity) == "user",
            "edit_messages": bool(getattr(admin, "edit_messages", False)),
        },
        "slowmode_seconds": getattr(full, "slowmode_seconds", None),
        "participants_count": getattr(full, "participants_count", None),
        "about": getattr(full, "about", None),
    }
```

`cli.py`: `p_info.add_argument("--full", action="store_true")`; dispatch: `fetch_info_full` when `args.full`. `to_rows` for full data: reuse base row (extra keys are JSON-only; document that in CONTRACT).

- [ ] **Step 4: Gates, CONTRACT ("`can` is best-effort preflight, not authorization truth — Telegram remains the authority"), commit**

```bash
git add src/tgcli/commands/info.py src/tgcli/cli.py tests/test_cli_info.py docs/CONTRACT.md
git commit -m "Add info --full role and rights preflight"
```

### Task 14: `tg doctor`

**Files:**
- Create: `src/tgcli/commands/doctor.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`
- Test: `tests/test_cli_doctor.py`

**Interfaces:**
- Produces: `doctor.run(config, alias=None) -> {"accounts": [...], "ok": bool}`; per account: `{"alias", "session", "checks": {"session_file", "lock_free", "state_writable", "authorized", ["error"]}, "user": {...}|None, "ok": bool}`. Read-only; always exits 0 when the check itself ran (failures live in the payload — CONTRACT documents this). No `--account` → all accounts. `doctor.check_account` uses `session.client` for the online part and reports `ConfigError` as a failed check, not a CLI error.

- [ ] **Step 1: Failing tests** in `tests/test_cli_doctor.py`:

```python
import json

import pytest

from tests.conftest import make_session_fake, ns
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.spare]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class DoctorClient:
    async def get_me(self):
        return ns(id=1, username="me", first_name="Me")


def _touch_session(tmp_path, name):
    from tgcli import session

    path = session.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_doctor_reports_all_accounts(config_env, monkeypatch, tmp_path, capsys):
    _touch_session(tmp_path, "main")
    make_session_fake(monkeypatch, DoctorClient())

    assert main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_alias = {item["alias"]: item for item in data["accounts"]}
    assert by_alias["main"]["ok"] is True
    assert by_alias["main"]["user"]["username"] == "me"
    assert by_alias["spare"]["checks"]["session_file"] is False
    assert by_alias["spare"]["ok"] is False
    assert data["ok"] is False


def test_doctor_single_account(config_env, monkeypatch, tmp_path, capsys):
    _touch_session(tmp_path, "main")
    make_session_fake(monkeypatch, DoctorClient())
    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [item["alias"] for item in data["accounts"]] == ["main"]
    assert data["ok"] is True
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_cli_doctor.py -q` → FAIL.

- [ ] **Step 3: Implement**

`src/tgcli/commands/doctor.py`:

```python
"""Read-only environment and session health checks (ADR-0028)."""

import fcntl
from pathlib import Path

from tgcli import safety, session
from tgcli.config import Config, resolve_account
from tgcli.errors import TgcliError


def _lock_free(session_file: Path) -> bool:
    try:
        handle = open(session_file.with_suffix(".lock"), "w")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        handle.close()


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".doctor-probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


async def check_account(account) -> dict:
    session_file = session.session_path(account)
    checks: dict = {
        "session_file": session_file.is_file(),
        "lock_free": _lock_free(session_file),
        "state_writable": _writable(safety.previews_dir()),
        "authorized": False,
    }
    user = None
    if checks["session_file"] and checks["lock_free"]:
        try:
            async with session.client(account) as tg:
                me = await tg.get_me()
                checks["authorized"] = me is not None
                if me is not None:
                    user = {
                        "id": me.id,
                        "username": getattr(me, "username", None),
                        "name": getattr(me, "first_name", None),
                    }
        except (TgcliError, OSError) as exc:
            checks["error"] = str(exc)
    ok = all(value for key, value in checks.items() if key != "error")
    return {
        "alias": account.alias,
        "session": str(session_file),
        "checks": checks,
        "user": user,
        "ok": ok,
    }


async def run(config: Config, alias: str | None = None) -> dict:
    if alias:
        accounts = [resolve_account(config, alias)]
    else:
        accounts = list(config.accounts.values())
    reports = [await check_account(account) for account in accounts]
    return {"accounts": reports, "ok": all(report["ok"] for report in reports)}


def to_rows(data: dict) -> list[tuple]:
    return [
        (
            report["alias"],
            "ok" if report["ok"] else "fail",
            (report["user"] or {}).get("username"),
            ", ".join(
                key for key, value in report["checks"].items()
                if key != "error" and not value
            ) or None,
        )
        for report in data["accounts"]
    ]
```

`cli.py`: parser `sub.add_parser("doctor", help="Check environment and session health", parents=[global_flags])`. Dispatch as a special branch **before** the generic `resolve_account` path (doctor manages its own sessions and reads all accounts when `--account` is absent):

```python
        elif args.command == "doctor":
            config = load_config()
            data = asyncio.run(
                asyncio.wait_for(
                    doctor_cmd.run(config, args.account), timeout=args.timeout
                )
            )
            rows = doctor_cmd.to_rows(data)
```

(placed alongside the `accounts`/`clone status` special cases; add `from tgcli.commands import doctor as doctor_cmd` to imports). Note: `make_session_fake` patches `cli.session.client`, and `doctor.check_account` calls `session.client` from `tgcli.session` — so in tests patch the same target the module uses: in `check_account` call it via `session.client(...)` and in the test use `monkeypatch.setattr("tgcli.commands.doctor.session.client", ...)`; that is what `make_session_fake` patches only for `cli`. Simplest: extend `make_session_fake` to patch both `cli.session.client` and `tgcli.session.client` (same function object either way) — patch `tgcli.session.client` once and both callers see it, since each module references the `session` module attribute at call time.

- [ ] **Step 4: Gates, CONTRACT (doctor JSON/TSV shape; "exit 0 = the check ran; consult `ok`"), MAP.md row, commit**

```bash
git add src/tgcli/commands/doctor.py src/tgcli/cli.py tests/test_cli_doctor.py tests/conftest.py docs/CONTRACT.md docs/MAP.md
git commit -m "Add doctor health report"
```

### Task 15: Docs closure

**Files:**
- Modify: `SKILL.md` (agent routing doc: new commands/flags, pagination recipes, retry-after-failure recipe for send), `docs/MAP.md` (verify every new module has its row), `docs/DEVLOG.md` (slice-3 entry)

- [ ] **Step 1:** Update `SKILL.md`: add `edit/delete/forward/mark-read/doctor` to the command list; add three recipes — "walk history" (`read --before-id` loop using `page.oldest_id`), "what's new since last check" (`dialogs --unread-only` → `read --after-id`), "send with retry" (`send --preview` → `--commit`; on network error re-run the same `--commit`).
- [ ] **Step 2:** Re-read `docs/MAP.md` against `src/tgcli/` — every added module (`confirm.py`, `commands/mutate.py`, `commands/doctor.py`) has a row.
- [ ] **Step 3:** Run all four gates one final time; append the DEVLOG entry; commit:

```bash
git add SKILL.md docs/MAP.md docs/DEVLOG.md
git commit -m "Update agent docs for v1.1 surface"
```

**End of slice 3:** merge/PR, then live smoke: `tg doctor --json`, `tg dialogs --unread-only --json`, `tg search --all` on a known string — visual acceptance with the owner.

---

## Out of Scope (ADR-0028)

Rejected: `tg spec`, `tg can`, `tg inbox`, keyed idempotency journal, opaque cursors. Deferred with triggers: MSG-001 (albums, scheduling, react, pin, protect, entities), FEED-001 (`tg changes`), ACCOUNTS-001 (`tg accounts login`). Clone is untouched.
