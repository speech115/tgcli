# Clone Chat Types Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tg clone` accepts bot dialogs, legacy basic groups, and forum megagroups as sources; forum sources clone into a private owned forum megagroup with a 1:1 topic map.

**Architecture:** Three slices in ascending complexity, each shippable alone. Slice 1 (task 1) lifts the bot gate. Slice 2 (tasks 2–3) adds `source_kind: "basic"` reusing the megagroup attribution transport. Slice 3 (tasks 4–7) adds `destination_kind`/`topic_map` state, a forum destination, lazy topic creation from topic-create service messages, and topic-aware message routing. Tasks 8–9 are docs and live acceptance.

**Tech Stack:** Python 3.12, Telethon (MTProto), pytest with fake clients (no network in tests), uv-managed venv.

**Spec:** `docs/superpowers/specs/2026-07-16-clone-chat-types-2-design.md` — read it before starting.

## Global Constraints

- Line budgets (hard, "cut before adding"): `src/tgcli/commands/clone.py` ≤ 400 (currently 390), `src/tgcli/clone/state.py` ≤ 170 (raised from 150 by ADR-0022), `src/tgcli/clone/attribution.py` ≤ 80 (currently 74), new `src/tgcli/clone/topics.py` ≤ 100, `src/tgcli/clone/profile.py` ≤ 75 (currently 43).
- Order fidelity is a hard requirement: oldest→newest single cursor, never reordered. Forum messages share the supergroup id space, so the cursor model must not change.
- Fail-closed state, no migrations: unknown enum values or versions raise `PolicyError`; new fields default for legacy files (pattern of ADR-0021 `source_kind`).
- No daemons, no background processes. One-shot CLI runs only.
- TDD: failing test first, watch it fail, minimal code, watch it pass. Full suite `pytest -q` green before every commit.
- Commit style: imperative English subject, no prefixes (match `git log`).
- All test code goes in the existing test files; follow their fixtures (`config_env`, `make_session_fake`, `seed_clone`, fake client classes).

---

### Task 1: Bot dialogs as source (slice 1)

**Files:**
- Modify: `src/tgcli/clone/attribution.py:11-15`
- Modify: `docs/CONTRACT.md` §11 (first paragraph, ~line 237)
- Test: `tests/test_cli_clone_init.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `attribution.source_kind(entity)` returns `"dialog"` for any `types.User`, including `bot=True`. Downstream (state allowlist, hybrid transport, `GetFullUserRequest` profile copy) is already generic over `"dialog"`.

- [x] **Step 1: Write the failing test**

In `tests/test_cli_clone_init.py`, remove the bot line from the reject parametrize and add an accept test:

```python
# In test_clone_init_preview_rejects_unsupported_source_kinds parametrize,
# DELETE this entry:
#        (user(bot=True), "bots are not supported"),

def test_clone_init_preview_accepts_bot_dialog(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = user(bot=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Alex Smith", "kind": "dialog"}
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_clone_init.py::test_clone_init_preview_accepts_bot_dialog -q`
Expected: FAIL — exit code 2 instead of 0 ("clone source bots are not supported").

- [x] **Step 3: Write minimal implementation**

In `src/tgcli/clone/attribution.py`, replace:

```python
    if isinstance(entity, types.User):
        if entity.bot:
            raise PolicyError("clone source bots are not supported")
        return "dialog"
```

with:

```python
    if isinstance(entity, types.User):
        return "dialog"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_clone_init.py -q && pytest -q`
Expected: all PASS.

- [x] **Step 5: Update CONTRACT.md §11**

In the §11 opening paragraph, change the two sentences:

> `It accepts broadcast channels, non-forum megagroup supergroups, and private one-to-one User dialogs. Forum supergroups, legacy basic groups, bots, and other peer shapes exit 2 with a source-specific policy message.`

to:

> `It accepts broadcast channels, non-forum megagroup supergroups, and private one-to-one User dialogs including dialogs with bots. Forum supergroups, legacy basic groups, and other peer shapes exit 2 with a source-specific policy message.`

(Tasks 3 and 8 rewrite this paragraph further; keep each edit truthful for the code as committed.)

- [x] **Step 6: Commit**

```bash
git add src/tgcli/clone/attribution.py tests/test_cli_clone_init.py docs/CONTRACT.md
git commit -m "Accept bot dialogs as clone sources"
```

---

### Task 2: Basic-group source gate and state (slice 2a)

**Files:**
- Modify: `src/tgcli/clone/attribution.py` (`source_kind`, `same_peer`)
- Modify: `src/tgcli/clone/state.py:96` (source-kind allowlist)
- Test: `tests/test_cli_clone_init.py`, `tests/test_clone_state.py`

**Interfaces:**
- Produces: `source_kind()` returns `"basic"` for a live `types.Chat`; raises `PolicyError` for migrated (`migrated_to` set) or `deactivated` chats. `same_peer(peer, source)` matches `types.PeerChat` against a `types.Chat` source (used by reply validation). `CloneState` round-trips `source_kind="basic"`.

- [x] **Step 1: Write the failing tests**

In `tests/test_cli_clone_init.py`, add a helper next to `user()` and update the matrix:

```python
def legacy_group(**overrides):
    values = {"id": 123, "title": "Legacy group", "photo": types.ChatPhotoEmpty(),
              "participants_count": 2, "date": None, "version": 1}
    values.update(overrides)
    return types.Chat(**values)
```

In `test_clone_init_preview_rejects_unsupported_source_kinds`, REPLACE the plain `types.Chat(...)` reject entry with these two:

```python
        (legacy_group(migrated_to=types.InputChannel(channel_id=555, access_hash=0)),
         "migrated to a supergroup"),
        (legacy_group(deactivated=True), "deactivated"),
```

Add the accept test:

```python
def test_clone_init_preview_accepts_basic_group(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = legacy_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Legacy group", "kind": "basic"}
```

In `tests/test_clone_state.py` (follow its existing state-dir fixture style):

```python
def test_state_accepts_basic_source_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(account_user_id=1, source_peer_id=2,
                                 source_title="Legacy", source_kind="basic")
    state.save(saved)
    assert state.load(saved.clone_id).source_kind == "basic"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_clone_init.py tests/test_clone_state.py -q`
Expected: new accept tests FAIL (policy error / `PolicyError: clone state ... is invalid`); migrated/deactivated entries FAIL because the current message is "basic groups are not supported".

- [x] **Step 3: Write minimal implementation**

`src/tgcli/clone/attribution.py` — replace the `types.Chat` branch:

```python
    if isinstance(entity, types.Chat):
        if getattr(entity, "migrated_to", None) is not None:
            raise PolicyError("clone source basic group migrated to a supergroup; "
                              "clone the supergroup instead")
        if getattr(entity, "deactivated", False):
            raise PolicyError("clone source basic group is deactivated")
        return "basic"
```

and extend `same_peer`:

```python
def same_peer(peer, source) -> bool:
    if isinstance(source, types.User):
        return isinstance(peer, types.PeerUser) and peer.user_id == source.id
    if isinstance(source, types.Chat):
        return isinstance(peer, types.PeerChat) and peer.chat_id == source.id
    return isinstance(peer, types.PeerChannel) and peer.channel_id == source.id
```

`src/tgcli/clone/state.py:96` — extend the allowlist:

```python
        if source_kind not in {"broadcast", "megagroup", "dialog", "basic"}:
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest -q`
Expected: all PASS. Also check the attribution budget: `wc -l src/tgcli/clone/attribution.py` must be ≤ 80.

- [x] **Step 5: Commit**

```bash
git add src/tgcli/clone/attribution.py src/tgcli/clone/state.py \
        tests/test_cli_clone_init.py tests/test_clone_state.py
git commit -m "Accept live basic groups as clone sources"
```

---

### Task 3: Basic-group profile copy and sync transport (slice 2b)

**Files:**
- Modify: `src/tgcli/clone/profile.py:12-18`
- Modify: `docs/CONTRACT.md` §11 (accepted-sources sentence)
- Test: `tests/test_cli_clone_init.py` (CloneInitClient + commit test), `tests/test_cli_clone_sync.py`

**Interfaces:**
- Consumes: `source_kind() == "basic"` from task 2.
- Produces: `profile.copy` handles `types.Chat` via `functions.messages.GetFullChatRequest(chat_id=...)`. Sync of a `"basic"` clone uses the existing attributed hybrid transport unchanged (it keys off `source_kind != "broadcast"`).

- [x] **Step 1: Write the failing tests**

`tests/test_cli_clone_init.py` — teach the fake client the new request (inside `CloneInitClient.__call__`, next to the GetFull* branches):

```python
        if isinstance(request, functions.messages.GetFullChatRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(about=self.source_about))
```

Add the commit test:

```python
def test_clone_init_commit_copies_basic_group_profile(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = legacy_group()
    client.source_about = "Group description"
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert client.destination.title == "Legacy group"
    assert client.destination.about == "Group description"
    assert any(isinstance(item, functions.messages.GetFullChatRequest)
               for item in client.requests)
    assert state.load(result["clone"]["id"]).source_kind == "basic"
```

`tests/test_cli_clone_sync.py` — add the same `legacy_group()` helper as in task 2 (module-level, next to `channel()`), then:

```python
def test_clone_sync_forwards_basic_group_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="basic", title="Legacy group")
    client = CloneSyncClient([message(2)])
    client.source = legacy_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert client.requests[0].drop_author is False
    assert sync["forwarded"] == 1


def test_clone_sync_reuploads_basic_group_reply_with_prefix(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    client = CloneReuploadClient([
        message(2, message="pong", from_id=types.PeerUser(77), sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1)),
    ])
    client.source = legacy_group()
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMessageRequest)]
    assert request.message == "Alex: pong"
    assert request.reply_to.reply_to_msg_id == 1001
    assert sync["reuploaded"] == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_clone_init.py::test_clone_init_commit_copies_basic_group_profile tests/test_cli_clone_sync.py -q`
Expected: the commit test FAILS inside `profile.copy` (a `types.Chat` falls into the `GetFullChannelRequest` branch and the fake client raises "unexpected request"). The two sync tests are expected to PASS already (the transport is generic) — if they pass on first run, that is the regression guard, keep them.

- [x] **Step 3: Write minimal implementation**

`src/tgcli/clone/profile.py` — replace the about-fetch block:

```python
    if isinstance(source, types.User):
        full = await cooldown(tg(functions.users.GetFullUserRequest(source)))
        about = getattr(full.full_user, "about", None) or ""
    elif isinstance(source, types.Chat):
        full = await cooldown(tg(functions.messages.GetFullChatRequest(
            chat_id=source.id)))
        about = getattr(full.full_chat, "about", None) or ""
    else:
        full = await cooldown(tg(functions.channels.GetFullChannelRequest(source)))
        about = getattr(full.full_chat, "about", None) or ""
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest -q`
Expected: all PASS.

- [x] **Step 5: Update CONTRACT.md §11**

Change the accepted-sources sentence to:

> `It accepts broadcast channels, non-forum megagroup supergroups, live legacy basic groups, and private one-to-one User dialogs including dialogs with bots. Forum supergroups, basic groups that migrated to a supergroup or were deactivated, and other peer shapes exit 2 with a source-specific policy message.`

- [x] **Step 6: Commit**

```bash
git add src/tgcli/clone/profile.py tests/test_cli_clone_init.py \
        tests/test_cli_clone_sync.py docs/CONTRACT.md
git commit -m "Copy basic-group profiles and cover attributed sync"
```

---

### Task 4: State fields for forum clones (slice 3a)

**Files:**
- Modify: `src/tgcli/clone/state.py`
- Test: `tests/test_clone_state.py`

**Interfaces:**
- Produces (used by tasks 5–7): `CloneState.destination_kind` (`"broadcast" | "forum"`, derived in `new()` from `source_kind`), `CloneState.topic_map: dict[str, int]`, `record_topic(source_topic_id, destination_topic_id)`, `topic_dest_for(source_topic_id) -> int | None`, and `max_destination_id()` covering both `id_map` and `topic_map` values (keeps tail verification quiet for forum destinations). `from_dict` allowlists add `"forum"` (source) and validate `destination_kind`; legacy files default to `"broadcast"` / `{}`.

- [x] **Step 1: Write the failing tests**

In `tests/test_clone_state.py`:

```python
def test_state_round_trips_forum_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(account_user_id=1, source_peer_id=2,
                                 source_title="Forum", source_kind="forum")
    saved.record_topic(7, 1007)
    state.save(saved)
    loaded = state.load(saved.clone_id)
    assert loaded.source_kind == "forum"
    assert loaded.destination_kind == "forum"
    assert loaded.topic_dest_for(7) == 1007
    assert loaded.max_destination_id() == 1007


def test_state_defaults_forum_fields_for_legacy_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(account_user_id=1, source_peer_id=2,
                                 source_title="Old")
    data = saved.to_dict()
    del data["destination_kind"], data["topic_map"]
    state.clones_dir().mkdir(parents=True)
    state.path_for(saved.clone_id).write_text(json.dumps(data))
    loaded = state.load(saved.clone_id)
    assert loaded.destination_kind == "broadcast"
    assert loaded.topic_map == {}


def test_state_rejects_unknown_destination_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    saved = state.CloneState.new(account_user_id=1, source_peer_id=2,
                                 source_title="Old")
    data = saved.to_dict()
    data["destination_kind"] = "group"
    state.clones_dir().mkdir(parents=True)
    state.path_for(saved.clone_id).write_text(json.dumps(data))
    with pytest.raises(PolicyError):
        state.load(saved.clone_id)
```

(Import `json`, `pytest`, `PolicyError` per the file's existing imports.)

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_clone_state.py -q`
Expected: FAIL — `TypeError` on `record_topic` / `KeyError: 'destination_kind'` / no `PolicyError`.

- [x] **Step 3: Write minimal implementation**

In `src/tgcli/clone/state.py`:

Add fields to the dataclass (after `source_kind`):

```python
    destination_kind: str = "broadcast"
    topic_map: dict[str, int] = field(default_factory=dict)
```

Derive in `new()` (add to the `cls(...)` call):

```python
            destination_kind="forum" if source_kind == "forum" else "broadcast",
```

Add helpers next to `record_mapping`/`dest_for`:

```python
    def record_topic(self, source_topic_id: int, destination_topic_id: int) -> None:
        self.topic_map[str(source_topic_id)] = destination_topic_id

    def topic_dest_for(self, source_topic_id: int) -> int | None:
        return self.topic_map.get(str(source_topic_id))
```

Replace `max_destination_id`:

```python
    def max_destination_id(self) -> int | None:
        values = [*self.id_map.values(), *self.topic_map.values()]
        return max(values) if values else None
```

Extend `to_dict()` (after `"source_kind"`):

```python
            "destination_kind": self.destination_kind,
            "topic_map": self.topic_map,
```

Extend `from_dict()` validation and constructor:

```python
        if source_kind not in {"broadcast", "megagroup", "dialog", "basic", "forum"}:
            raise ValueError("invalid source kind")
        destination_kind = data.get("destination_kind", "broadcast")
        if destination_kind not in {"broadcast", "forum"}:
            raise ValueError("invalid destination kind")
```

and pass `destination_kind=destination_kind, topic_map=dict(data.get("topic_map", {}))` to `cls(...)`.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest -q && wc -l src/tgcli/clone/state.py`
Expected: all PASS; state.py ≤ 170 lines.

- [x] **Step 5: Commit**

```bash
git add src/tgcli/clone/state.py tests/test_clone_state.py
git commit -m "Add destination kind and topic map to clone state"
```

---

### Task 5: Forum source gate and forum destination (slice 3b)

**Files:**
- Create: `src/tgcli/clone/topics.py`
- Modify: `src/tgcli/clone/attribution.py:18-21`
- Modify: `src/tgcli/commands/clone.py` (`_marker_candidates`, `commit_init`, `sync_text` shape check, imports)
- Test: `tests/test_cli_clone_init.py`

**Interfaces:**
- Consumes: task 4 state fields.
- Produces: `attribution.source_kind()` returns `"forum"` for `megagroup=True, forum=True`. `topics.GENERAL_TOPIC_ID = 1`; `topics.is_forum_destination(entity, *, title=None) -> bool`; `topics.create_request(marker) -> CreateChannelRequest` (megagroup); `async topics.ensure_forum(mutate, destination)` toggles the forum flag idempotently. `commit_init` creates/adopts the destination by kind and `sync_text` validates it by kind.

- [x] **Step 1: Write the failing tests**

`tests/test_cli_clone_init.py`:

DELETE the forum entry from `test_clone_init_preview_rejects_unsupported_source_kinds` parametrize (the `channel(123, "Forum", ...)` line).

Teach `CloneInitClient.__call__`: replace the `CreateChannelRequest` branch body line

```python
            self.destination = channel(999, request.title, creator=True)
```

with

```python
            self.destination = channel(999, request.title, creator=True,
                                       broadcast=request.broadcast,
                                       megagroup=request.megagroup, forum=False)
```

and add a new branch:

```python
        if isinstance(request, functions.channels.ToggleForumRequest):
            self.destination.forum = True
            return SimpleNamespace()
```

Add tests:

```python
def test_clone_init_preview_accepts_forum_megagroup(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = channel(123, "Forum chat", broadcast=False, megagroup=True,
                            forum=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Forum chat", "kind": "forum"}


def test_clone_init_commit_creates_forum_destination(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = channel(123, "Forum chat", broadcast=False, megagroup=True,
                            forum=True)
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    [created] = [item for item in client.requests
                 if isinstance(item, functions.channels.CreateChannelRequest)]
    assert created.megagroup is True and created.broadcast is False
    assert any(isinstance(item, functions.channels.ToggleForumRequest)
               for item in client.requests)
    assert client.destination.forum is True
    saved = state.load(result["clone"]["id"])
    assert saved.source_kind == "forum"
    assert saved.destination_kind == "forum"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_clone_init.py -q`
Expected: both new tests FAIL (exit 2, "forum topics are not supported").

- [x] **Step 3: Write minimal implementation**

`src/tgcli/clone/attribution.py` — replace the megagroup branch:

```python
    if getattr(entity, "megagroup", False):
        return "forum" if getattr(entity, "forum", False) else "megagroup"
```

Create `src/tgcli/clone/topics.py`:

```python
"""Forum destinations and topic maps for forum clones (ADR-0022)."""

from telethon.tl import functions

GENERAL_TOPIC_ID = 1


def is_forum_destination(entity, *, title: str | None = None) -> bool:
    active = any(getattr(item, "active", False)
                 for item in (getattr(entity, "usernames", None) or ()))
    return bool((title is None or getattr(entity, "title", None) == title)
                and getattr(entity, "creator", False)
                and getattr(entity, "megagroup", False)
                and not getattr(entity, "broadcast", False)
                and getattr(entity, "username", None) is None and not active)


def create_request(marker: str):
    return functions.channels.CreateChannelRequest(
        title=marker, about="", broadcast=False, megagroup=True)


async def ensure_forum(mutate, destination) -> None:
    if not getattr(destination, "forum", False):
        await mutate(functions.channels.ToggleForumRequest(
            channel=destination, enabled=True))
        destination.forum = True
```

`src/tgcli/commands/clone.py`:

Add `topics` to the clone-package import (line 11):

```python
from tgcli.clone import attribution, fidelity, profile, replies, state, topics
```

Give `_marker_candidates` a shape parameter:

```python
async def _marker_candidates(tg, marker: str, shape_ok) -> tuple[list[object], list[object]]:
    ...
        (valid if shape_ok(entity, title=marker) else wrong_shape).append(entity)
```

In `commit_init`, right after `_enforce_cooldown(clone_state)`:

```python
    forum = clone_state.destination_kind == "forum"
    shape_ok = topics.is_forum_destination if forum else _is_private_owned_broadcast
    kind_name = "forum megagroup" if forum else "broadcast channel"
```

Then replace the shape checks and creation call:

```python
        if not shape_ok(destination):
            raise PolicyError(f"clone destination is not a private owned {kind_name}")
```

```python
        valid, wrong_shape = await _marker_candidates(tg, marker, shape_ok)
```

```python
            update = await _mutate(
                tg, topics.create_request(marker) if forum else
                functions.channels.CreateChannelRequest(
                    title=marker, about="", broadcast=True, megagroup=False),
                clone_state)
            candidates = [item for item in getattr(update, "chats", ())
                          if shape_ok(item, title=marker)]
```

After the destination is resolved (both adopt and create paths, right after `state.save(clone_state)` records `destination_peer_id`):

```python
    if forum:
        await topics.ensure_forum(
            lambda request: _mutate(tg, request, clone_state), destination)
```

In `sync_text`, replace the destination shape check:

```python
    forum = clone_state.destination_kind == "forum"
    if not (topics.is_forum_destination(destination) if forum
            else _is_private_owned_broadcast(destination)):
        kind_name = "forum megagroup" if forum else "broadcast channel"
        raise PolicyError(f"clone destination is not a private owned {kind_name}")
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest -q`
Expected: all PASS (existing broadcast-shape error-message tests still match).

- [x] **Step 5: Commit**

```bash
git add src/tgcli/clone/topics.py src/tgcli/clone/attribution.py \
        src/tgcli/commands/clone.py tests/test_cli_clone_init.py
git commit -m "Create forum megagroup destinations for forum clones"
```

---

### Task 6: Lazy topic creation during sync (slice 3c)

**Files:**
- Modify: `src/tgcli/clone/topics.py`
- Modify: `src/tgcli/commands/clone.py` (move `_confirmed_destination_ids` out; sync loop service branch; sync report + `sync_rows`)
- Test: `tests/test_cli_clone_sync.py` (also update the two existing full-shape assertions)

**Interfaces:**
- Consumes: task 4 `record_topic`/`topic_dest_for`, task 5 `topics.py`.
- Produces: `topics.confirmed_destination_ids(response, random_ids)` (moved verbatim from `commands/clone.py`; clone.py now calls `topics.confirmed_destination_ids(...)`). `async topics.create_topic(mutate, destination, clone_state, source_topic_id, *, account_alias, title, icon_color=None, icon_emoji_id=None) -> int` — audits fail-closed, sends `messages.CreateForumTopicRequest`, confirms the new topic root id, records the mapping, and saves state. Sync report gains `"topics_created": <int>` (always present, 0 for non-forum clones); `sync_rows` gains the column after the unsupported count.

- [x] **Step 1: Write the failing tests**

In `tests/test_cli_clone_sync.py` add helpers (module level, after `CloneReuploadClient`):

```python
def forum_channel(channel_id, title, **overrides):
    return channel(channel_id, title, broadcast=False, megagroup=True,
                   forum=True, **overrides)


def topic_create(message_id, title):
    return message(message_id, message=None,
                   action=types.MessageActionTopicCreate(title=title, icon_color=0))


class CloneForumClient(CloneReuploadClient):
    def __init__(self, messages):
        super().__init__(messages)
        self.source = forum_channel(123, "Forum chat")
        self.destination = forum_channel(999, "Forum chat", creator=True)
        self.source_topic_titles = {}

    async def __call__(self, request):
        if isinstance(request, functions.messages.CreateForumTopicRequest):
            self.requests.append(request)
            self.destination_last_id += 1
            self.destination_actions[self.destination_last_id] = (
                types.MessageActionTopicCreate(title=request.title, icon_color=0))
            return SimpleNamespace(updates=[types.UpdateMessageID(
                id=self.destination_last_id, random_id=request.random_id)])
        if isinstance(request, functions.messages.GetForumTopicsByIDRequest):
            self.requests.append(request)
            return SimpleNamespace(topics=[
                SimpleNamespace(id=topic_id, title=self.source_topic_titles[topic_id])
                for topic_id in request.topics
                if topic_id in self.source_topic_titles])
        return await super().__call__(request)
```

Add the test:

```python
def test_clone_sync_creates_destination_topic_from_topic_create_service(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([topic_create(2, "News")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.CreateForumTopicRequest)
    assert request.title == "News"
    assert sync["topics_created"] == 1
    assert sync["skipped_service"] == 0
    assert sync["copied"] == 0
    saved = state.load(clone_state.clone_id)
    assert saved.topic_dest_for(2) == 2
    assert saved.cursor == 2

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)["sync"]
    assert rerun["topics_created"] == 0
    assert len(client.requests) == 1
```

Update the two existing full-shape assertions:
- `test_clone_sync_copies_plain_text_oldest_first_and_reruns_idempotently`: add `"topics_created": 0,` to the expected `result["sync"]` dict.
- `test_clone_sync_plain_output_has_contract_columns`: add the `topics_created` column value (`0`) at the position matching `sync_rows` below.

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_clone_sync.py -q`
Expected: new test FAILS (`KeyError: 'topics_created'` or unexpected-request assertion); the two updated tests FAIL on the missing key/column.

- [x] **Step 3: Write minimal implementation**

Move `_confirmed_destination_ids` (clone.py:166-183) verbatim into `topics.py` as `confirmed_destination_ids` (add `from telethon.tl import functions, types`, `from tgcli import safety`, and `from tgcli.errors import PolicyError` to topics.py imports); in clone.py replace its two call sites with `topics.confirmed_destination_ids(...)` and delete the function.

Append to `topics.py`:

```python
import secrets

from tgcli.clone import state


async def create_topic(mutate, destination, clone_state, source_topic_id, *,
                       account_alias: str, title: str, icon_color=None,
                       icon_emoji_id=None) -> int:
    random_id = secrets.randbelow(2**63 - 1) + 1
    safety.append_audit("clone-sync-topic", account_alias, {
        "clone_id": clone_state.clone_id, "source_topic_id": source_topic_id})
    response = await mutate(functions.messages.CreateForumTopicRequest(
        peer=destination, title=title, random_id=random_id,
        icon_color=icon_color, icon_emoji_id=icon_emoji_id))
    [destination_topic_id] = confirmed_destination_ids(response, [random_id])
    clone_state.record_topic(source_topic_id, destination_topic_id)
    state.save(clone_state)
    return destination_topic_id
```

(Keep all imports at the top of the file; shown here inline for locality.)

In `sync_text` (clone.py): add near the other counters

```python
    topic_counters = {"topics_created": 0}
    mutate = lambda request: _mutate(tg, request, clone_state)
```

and replace the service-message branch body (after the album-flush and limit checks, keeping them intact):

```python
            if forum and isinstance(source_message.action,
                                    types.MessageActionTopicCreate):
                if clone_state.topic_dest_for(source_message.id) is None:
                    await topics.create_topic(
                        mutate, destination, clone_state, source_message.id,
                        account_alias=account_alias,
                        title=source_message.action.title,
                        icon_color=getattr(source_message.action, "icon_color", None),
                        icon_emoji_id=getattr(source_message.action, "icon_emoji_id", None))
                    topic_counters["topics_created"] += 1
            else:
                skipped_service += 1
            clone_state.cursor = source_message.id
            state.save(clone_state)
            continue
```

Add `**topic_counters,` to the returned `"sync"` dict (next to `**transport_counts`), and add the column to `sync_rows` after the unsupported count:

```python
    return [(sync["copied"], sync["forwarded"], sync["reuploaded"], sync["snapshots"],
             sync["reply_flattened"], sync["skipped_service"], len(sync["skipped_unsupported"]),
             sync["topics_created"], sync["cursor"], clone["id"], clone["source"]["id"],
             clone["destination"]["id"], sync["more"])]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest -q && wc -l src/tgcli/commands/clone.py src/tgcli/clone/topics.py`
Expected: all PASS; clone.py shrinks (function moved out), topics.py ≤ 100.

- [x] **Step 5: Commit**

```bash
git add src/tgcli/clone/topics.py src/tgcli/commands/clone.py tests/test_cli_clone_sync.py
git commit -m "Create destination topics lazily from topic-create messages"
```

---

### Task 7: Topic-aware message routing (slice 3d)

**Files:**
- Modify: `src/tgcli/clone/replies.py` (forum reply headers)
- Modify: `src/tgcli/clone/topics.py` (`topic_id_of`, `ensure_topic`, `place`, `placement_only`)
- Modify: `src/tgcli/commands/clone.py` (`finish_batch`, `_forward_batch`)
- Test: `tests/test_cli_clone_sync.py`

**Interfaces:**
- Consumes: tasks 4–6.
- Produces: `topics.topic_id_of(message) -> int` (source topic id; `GENERAL_TOPIC_ID` when no forum header). `async topics.ensure_topic(mutate, source, destination, clone_state, source_topic_id, counters, *, account_alias) -> int` (General passthrough; map hit; else one `GetForumTopicsByIDRequest` lookup + audited `create_topic`, counted in `counters["topics_created"]`). `topics.place(reply_to, destination_topic_id)` merges topic placement into an `InputReplyToMessage` (General → unchanged). `topics.placement_only(header) -> bool`. `replies.target` accepts forum headers only for forum clones: placement-only headers (no `reply_to_top_id`) are not replies; real in-topic replies map the parent via `id_map` with `top_msg_id` left to `place()`.

- [x] **Step 1: Verify the forward-topic-targeting deferred check (spec open question)**

Run:
```bash
python -c "import inspect; from telethon.tl import functions; \
print('top_msg_id' in inspect.signature(functions.messages.ForwardMessagesRequest.__init__).parameters)"
```
Expected: `True`. If `False`: forum batches with a non-General topic must force the reupload transport instead of passing `top_msg_id` (add `or topic_dest not in (None, topics.GENERAL_TOPIC_ID)` to the `reupload` condition, skip the `top_msg_id=` argument), and record the outcome in ADR-0022 (task 8). All steps below assume `True`.

- [x] **Step 2: Write the failing tests**

In `tests/test_cli_clone_sync.py`:

```python
def test_clone_sync_forwards_topic_message_into_mapped_topic(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneForumClient([
        message(3, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.top_msg_id == 1002
    assert request.drop_author is False
    assert sync["forwarded"] == 1
    assert sync["reply_flattened"] == 0


def test_clone_sync_forwards_general_topic_message_without_top_id(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.top_msg_id is None


def test_clone_sync_reuploads_topic_reply_with_prefix_into_topic(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.record_mapping(3, 1003)
    clone_state.cursor = 3
    state.save(clone_state)
    client = CloneForumClient([
        message(4, message="pong", from_id=types.PeerUser(77), sender_id=77,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=3, reply_to_top_id=2, forum_topic=True)),
    ])
    client.destination_last_id = 1003

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMessageRequest)]
    assert request.message == "Alex: pong"
    assert request.reply_to.reply_to_msg_id == 1003
    assert request.reply_to.top_msg_id == 1002
    assert sync["reuploaded"] == 1


def test_clone_sync_recovers_unmapped_topic_from_source_lookup(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    lookups = [item for item in client.requests
               if isinstance(item, functions.messages.GetForumTopicsByIDRequest)]
    created = [item for item in client.requests
               if isinstance(item, functions.messages.CreateForumTopicRequest)]
    assert len(lookups) == 1 and len(created) == 1
    assert created[0].title == "Old news"
    assert sync["topics_created"] == 1
    forward = [item for item in client.requests
               if isinstance(item, functions.messages.ForwardMessagesRequest)]
    assert forward[0].top_msg_id == 2  # topic root created as destination id 2


def test_clone_sync_rejects_forum_reply_header_for_nonforum_clone(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient([
        message(2, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=1, forum_topic=True)),
    ])
    client.source = channel(123, "Team chat", broadcast=False, megagroup=True,
                            forum=False)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply shape is not supported" in capsys.readouterr().err
```

- [x] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_cli_clone_sync.py -q`
Expected: forum routing tests FAIL (today `replies._signature` raises "clone reply shape is not supported" on `forum_topic`; the reject regression test may already PASS — keep it as the guard).

- [x] **Step 4: Write minimal implementation**

`src/tgcli/clone/replies.py` — thread a `forum` flag:

```python
def _signature(header, source, forum=False):
```

Remove `header.forum_topic` from the combined unsupported `if`, then insert after it:

```python
    if header.forum_topic:
        if not forum:
            raise PolicyError("clone reply shape is not supported")
        if header.reply_to_top_id is None:
            return None
        parent_id, top_id = header.reply_to_msg_id, None
    else:
        parent_id, top_id = header.reply_to_msg_id, header.reply_to_top_id
```

(and drop the old `parent_id, top_id = header.reply_to_msg_id, header.reply_to_top_id` line). In `target`:

```python
def target(messages, clone_state, source):
    forum = clone_state.destination_kind == "forum"
    signatures = [_signature(getattr(message, "reply_to", None), source, forum)
                  for message in messages]
```

`src/tgcli/clone/topics.py` — append:

```python
def topic_id_of(message) -> int:
    header = getattr(message, "reply_to", None)
    if header is None or not getattr(header, "forum_topic", False):
        return GENERAL_TOPIC_ID
    return header.reply_to_top_id or header.reply_to_msg_id


def placement_only(header) -> bool:
    return bool(header is not None and getattr(header, "forum_topic", False)
                and header.reply_to_top_id is None)


async def ensure_topic(mutate, source, destination, clone_state,
                       source_topic_id: int, counters: dict, *,
                       account_alias: str) -> int:
    if source_topic_id == GENERAL_TOPIC_ID:
        return GENERAL_TOPIC_ID
    known = clone_state.topic_dest_for(source_topic_id)
    if known is not None:
        return known
    response = await mutate(functions.messages.GetForumTopicsByIDRequest(
        peer=source, topics=[source_topic_id]))
    found = [item for item in getattr(response, "topics", ())
             if getattr(item, "id", None) == source_topic_id
             and isinstance(getattr(item, "title", None), str)]
    title = found[0].title if found else f"topic {source_topic_id}"
    counters["topics_created"] += 1
    return await create_topic(mutate, destination, clone_state, source_topic_id,
                              account_alias=account_alias, title=title)


def place(reply_to, destination_topic_id: int):
    if destination_topic_id == GENERAL_TOPIC_ID:
        return reply_to
    if reply_to is None:
        return types.InputReplyToMessage(reply_to_msg_id=destination_topic_id)
    reply_to.top_msg_id = destination_topic_id
    return reply_to
```

`src/tgcli/commands/clone.py`:

`_forward_batch` — add a `topic_dest=None` keyword parameter. Replace the `reply_flattened` line with:

```python
    header = getattr(messages[0], "reply_to", None)
    reply_flattened = (header is not None and reply_to is None
                       and not topics.placement_only(header))
```

After the `reupload = (...)` decision (transport must not be forced by mere placement), insert:

```python
    if topic_dest is not None:
        reply_to = topics.place(reply_to, topic_dest)
    top_msg_id = None if topic_dest in (None, topics.GENERAL_TOPIC_ID) else topic_dest
```

and add `top_msg_id=top_msg_id,` to the `ForwardMessagesRequest(...)` construction.

In `sync_text`'s `finish_batch`, before calling `_forward_batch`:

```python
        topic_dest = None
        if forum:
            topic_dest = await topics.ensure_topic(
                mutate, source_entity, destination, clone_state,
                topics.topic_id_of(messages[0]), topic_counters,
                account_alias=account_alias)
```

and pass `topic_dest=topic_dest` to `_forward_batch`.

- [x] **Step 5: Run tests to verify they pass**

Run: `pytest -q`
Expected: all PASS, including every pre-existing reply/album/transport regression.

- [x] **Step 6: Budget checkpoint**

Run: `wc -l src/tgcli/commands/clone.py src/tgcli/clone/topics.py src/tgcli/clone/state.py src/tgcli/clone/attribution.py src/tgcli/clone/replies.py`

Caps: clone.py ≤ 400, topics.py ≤ 100, state.py ≤ 170, attribution.py ≤ 80. If clone.py exceeds 400, cut in this order until under: (1) inline `_init_result` into its single call site in `commit_init`; (2) collapse the two `kind_name`/PolicyError pairs into a shared one-line f-string; (3) fold `_entry`'s dict literal formatting tighter. Do NOT move transport code to new modules — the spec fixes the module layout.

- [x] **Step 7: Commit**

```bash
git add src/tgcli/clone/replies.py src/tgcli/clone/topics.py \
        src/tgcli/commands/clone.py tests/test_cli_clone_sync.py
git commit -m "Route forum clone messages into mapped destination topics"
```

---

### Task 8: Documentation — CONTRACT, ADR-0022, ISSUES, MAP, PLAN

**Files:**
- Modify: `docs/CONTRACT.md` §11
- Create: `docs/decisions/ADR-0022-clone-forum-topics.md`
- Modify: `docs/ISSUES.md` (CLONE-002)
- Modify: `docs/MAP.md`, `docs/PLAN.md`

**Interfaces:** none (docs describe tasks 1–7 exactly as committed).

- [ ] **Step 1: Rewrite CONTRACT.md §11 header paragraph**

Replace the opening paragraph (through "…private owned broadcast channel.") with:

> `tg clone` is the canonical chat-copy surface. It accepts broadcast channels, megagroup supergroups (forum and non-forum), live legacy basic groups, and private one-to-one User dialogs including dialogs with bots. Basic groups that migrated to a supergroup or were deactivated, and other peer shapes, exit 2 with a source-specific policy message. The destination type follows the source kind: a forum source clones into a private owned forum megagroup with a 1:1 topic map; every other source clones into a private owned broadcast channel. Destinations are tool-created and tool-controlled; cloning into pre-existing or shared groups is not supported.

Also in §11: update the sync JSON example and plain-column list to include `topics_created` (position matches `sync_rows`: after the unsupported count, before `cursor`), and add one sentence to the sync semantics: *"For forum clones, a topic-create service message creates the matching destination topic (counted in `topics_created`, not `skipped_service`); messages arriving for an unmapped topic recover it from the source topic's current title."*

- [ ] **Step 2: Write ADR-0022**

Create `docs/decisions/ADR-0022-clone-forum-topics.md`:

```markdown
# ADR-0022: Clone forum sources into forum megagroups with a topic map

Date: 2026-07-16
Status: accepted
Builds on: ADR-0017 (clone), ADR-0021 (attributed sources).
Spec: docs/superpowers/specs/2026-07-16-clone-chat-types-2-design.md

## Decision

- The CONTRACT §11 invariant "destination is always a private owned broadcast
  channel" is replaced by "destination type follows source kind": forum
  megagroup sources clone into a private owned **forum megagroup**; every
  other source keeps the broadcast-channel destination. No `--dest-type`
  flag; no cloning into pre-existing groups.
- Topics map 1:1 through `topic_map` (source topic id → destination topic
  root id) in the per-clone state, created **lazily during sync**: a
  `MessageActionTopicCreate` service message creates the destination topic
  (title + icon color/emoji); a message for an unmapped topic recovers the
  title via one `GetForumTopicsByIDRequest` and creates it on first use.
  The General topic (id 1) always maps to the destination's General topic
  and is never created.
- Order fidelity is unchanged: forum messages share the supergroup's single
  id space, so the existing single oldest→newest cursor already interleaves
  topics in exact source order.
- Reply shapes: placement-only forum headers (no `reply_to_top_id`) are
  topic placement, not replies — they do not force the reupload transport
  and do not count as `reply_flattened`. Real in-topic replies map the
  parent through `id_map` and the topic through `topic_map`. Non-forum
  clones still reject forum reply headers fail-closed.
- Transport: native forwards target topics via `ForwardMessagesRequest
  .top_msg_id` (verified against the installed Telethon layer at
  implementation time; the designed fallback — reupload for non-General
  topic batches — was not needed).
- Crash model: `create_topic` saves state immediately after confirmation;
  a hard crash between the Telegram call and the save can duplicate at most
  one destination topic, visible and manually deletable — same accepted
  trade-off as ADR-0017's batch crash model.
- Budgets: new `clone/topics.py` ≤ 100 lines (owns forum destination shape,
  topic map, and batch confirmation extraction); `clone/state.py` raised
  150 → 170 for `destination_kind` + `topic_map`. `commands/clone.py`
  stays ≤ 400.

## Out of scope

Topic edit/close/hide propagation (`MessageActionTopicEdit` stays a skipped
service message), closed/hidden flags on created topics (v1 creates open),
cloning into existing groups, comments/watch.
```

(Fix the typo "destination ation" when writing the file; adjust the transport line per task 7 step 1's outcome.)

- [ ] **Step 3: Close CLONE-002 in docs/ISSUES.md**

Replace the CLONE-002 body with:

```markdown
## CLONE-002 — Forum topics and legacy groups

**Status:** closed by ADR-0022 (2026-07-16).

Forum megagroups clone into forum-megagroup destinations with a 1:1 lazy
topic map; live legacy basic groups clone like megagroups (broadcast
destination, attributed transport); bot dialogs are accepted dialog sources.
Migrated or deactivated basic groups are rejected with pointer messages.
Still out of scope: cloning into pre-existing groups, secret chats,
topic edit/close propagation.
```

- [ ] **Step 4: Update MAP.md and PLAN.md**

- MAP.md: add a one-line entry for `src/tgcli/clone/topics.py` next to the other `clone/` modules, matching the file's existing format: "forum destination shape, lazy topic map, batch confirmation (ADR-0022)".
- PLAN.md: in the current-phase section, note that clone accepts bots/basic groups/forums per ADR-0022 and that the destination invariant is now kind-dependent.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q` (docs only — suite must still be green).

```bash
git add docs/CONTRACT.md docs/decisions/ADR-0022-clone-forum-topics.md \
        docs/ISSUES.md docs/MAP.md docs/PLAN.md
git commit -m "Document clone chat types round 2 (ADR-0022)"
```

---

### Task 9: Live acceptance and DEVLOG

**Files:**
- Modify: `docs/DEVLOG.md` (new dated entry)
- Modify: `docs/superpowers/plans/2026-07-16-clone-chat-types-2.md` (record results)

**Interfaces:** none — this is the release gate (user rule: visual acceptance, not test counts).

Live fixtures are user-provided — coordinate before running: a real bot dialog, a small owned legacy basic group, and an owned forum with ≥3 topics (messages in General, in two named topics, and at least one in-topic reply). Reuse existing demo peers where possible — creating many channels triggers ~15h FLOOD_WAIT (live gotcha from DEVLOG 2026-07-15). Sessions/accounts per `tg accounts`.

- [ ] **Step 1: Bot dialog (slice 1 gate)**

```bash
tg clone init <bot-ref> --json           # expect kind "dialog", exit 0
tg clone init <bot-ref> --commit <preview_id> --json
tg clone sync <bot-ref> --json
tg clone sync <bot-ref> --json           # rerun: copied 0
```
Visually verify in Telegram: order matches, both authors visible (forward headers / prefixes).

- [ ] **Step 2: Basic group (slice 2 gate)**

Same four commands with the basic-group ref; expect kind `"basic"`, destination is a broadcast channel, attribution prefixes on reply reuploads, rerun copies 0.

- [ ] **Step 3: Forum (slice 3 gate)**

Same four commands with the forum ref; expect kind `"forum"`. Visually verify: destination is a forum; topics exist 1:1 with correct titles; every message sits in the topic mirroring its source topic; General content is in General; the in-topic reply links to the right parent; `topics_created` matches; rerun copies 0 and creates 0 topics. **This step also confirms the task 7 step 1 forward-targeting decision on live data — if forwards misroute topics, switch to the designed reupload fallback, update ADR-0022, and re-run.**

- [ ] **Step 4: Record results**

Append the observed numbers (mapped counts, topics created, rerun zeros, any live discoveries) to this plan under a `### Live results` heading, add the DEVLOG entry for the session, and commit:

```bash
git add docs/DEVLOG.md docs/superpowers/plans/2026-07-16-clone-chat-types-2.md
git commit -m "Record clone chat types round 2 live acceptance"
```
