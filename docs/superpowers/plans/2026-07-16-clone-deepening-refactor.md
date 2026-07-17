# Clone Deepening Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Behavior-frozen refactor of the clone subsystem: extract a pure batch
planner from `sync_text`, turn the forward/reupload/snapshot decision into an
explicit pure `TransportPlan`, split poll/story rendering out of `fidelity.py`,
inline `profile.py`, and backfill direct unit tests for the pure clone helpers.

**Architecture:** Two new deep modules with small interfaces —
`clone/batching.py` (async generator: message stream → `Batch`/`ServiceSkip`
events; no Telethon) and `clone/transport.py` (pure `decide()` →
`TransportPlan`). `commands/clone.py` keeps orchestration and side effects
(network, audit, state saves) but consumes those interfaces instead of inlining
the logic. `fidelity.py` keeps only media capability classification; rendering
moves to `clone/snapshot.py`. External behavior (JSON output, exit codes,
audit records, state files) is frozen — the existing CLI-level tests are the
acceptance net and must stay green untouched.

**Tech Stack:** Python 3.12, Telethon 1.44.0, pytest (uv-managed venv).

## Global Constraints

- **Behavior freeze:** no change to JSON shapes, exit codes, audit event names,
  state file schema (`version: 1`), or the order of Telegram calls. Existing
  tests in `tests/test_cli_clone_*.py` and `tests/test_clone_state.py` must
  pass **unmodified** (except import-path fixes explicitly listed in a task).
- **Sequencing gate:** execute only after the round-2 branch
  (`codex/clone-chat-types-round-2-tasks-1-3`, plan
  `2026-07-16-clone-chat-types-2.md`) is merged to main. Task 0 reconciles
  drift — all code snippets below were written against main `62866d3`.
- **Before round 3:** this plan must land before implementing
  `docs/superpowers/specs/2026-07-16-clone-comments-design.md` (the comments
  loop becomes the second consumer of `batching.plan`).
- No new dependencies. No daemons. No behavior flags.
- Work in an isolated worktree (superpowers:using-git-worktrees).
- `pytest -q` green at the end of every task; commit at the end of every task.
- Docs discipline (ADR-0007): MAP.md updated in the task that changes the tree;
  DEVLOG entry in the final task.

---

### Task 0: Preflight — reconcile with round-2 drift

**Files:**
- Read: `src/tgcli/commands/clone.py`, `src/tgcli/clone/*.py`

**Interfaces:**
- Consumes: merged main containing round-2 (chat types) work.
- Produces: a confirmed snippet-drift assessment for Tasks 1–7.

- [x] **Step 1: Confirm round-2 is merged**

Run: `git log --oneline -10`
Expected: a merge/squash commit for `clone-chat-types` round 2 is present on
main. If it is not, STOP — this plan is blocked (Global Constraints).

- [x] **Step 2: Baseline test run**

Run: `pytest -q`
Expected: PASS (all tests green before any change).

- [x] **Step 3: Review drift against snippet base**

Run: `git diff 62866d3..HEAD --stat -- src/tgcli/commands/clone.py src/tgcli/clone/`
Then read the changed regions of `sync_text`, `_forward_batch`, and
`_reupload_batch` in full.

Drift rules for the tasks below:
- If round 2 added parameters threaded through the batch path (e.g. topic
  routing from tasks 6–7 of the round-2 plan: a per-batch destination topic /
  `reply_to` top id), keep them flowing through `copy_batch` and
  `_forward_batch` unchanged. `batching.Batch` stays a plain message
  container — topic resolution is orchestration, not batching.
- If `_forward_batch`'s decision inputs gained new terms (e.g. new source
  kinds), add them to `transport.decide()` as pure inputs in Task 3, mirroring
  the merged code's boolean logic exactly.
- If drift is structural (the loop no longer looks like `active_album` +
  `finish_batch`), stop and regenerate this plan instead of forcing it.

---

### Task 1: `clone/batching.py` — pure batch planner

**Files:**
- Create: `src/tgcli/clone/batching.py`
- Test: `tests/test_clone_batching.py`

**Interfaces:**
- Consumes: nothing (pure; no Telethon imports).
- Produces:
  - `@dataclass(frozen=True) ServiceSkip(message_id: int)`
  - `@dataclass(frozen=True) Batch(messages: tuple)`
  - `async def plan(messages) -> AsyncIterator[ServiceSkip | Batch]` where
    `messages` is any async iterator of Telethon-shaped message objects
    (attributes used: `.id`, `.action`, `.grouped_id`).

Event contract (must match today's `sync_text` loop exactly):
- service message (`.action is not None`) flushes any open album, then yields
  `ServiceSkip(message.id)`;
- consecutive messages with the same valid `grouped_id` accumulate into one
  album; a differing `grouped_id`, a single, or a service message flushes it;
- a single message yields `Batch((message,))`;
- stream end flushes a trailing album;
- a `grouped_id` that is a `bool` or not an `int` raises
  `PolicyError("clone album group id is invalid")`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_clone_batching.py
"""Unit tests for the pure clone batch planner."""
import asyncio
from types import SimpleNamespace

import pytest

from tgcli.clone import batching
from tgcli.errors import PolicyError


def _msg(message_id, *, action=None, grouped_id=None):
    return SimpleNamespace(id=message_id, action=action, grouped_id=grouped_id)


def _events(messages):
    async def stream():
        for message in messages:
            yield message

    async def collect():
        return [event async for event in batching.plan(stream())]

    return asyncio.run(collect())


def _shape(events):
    return [
        (type(event).__name__,
         event.message_id if isinstance(event, batching.ServiceSkip)
         else [message.id for message in event.messages])
        for event in events
    ]


def test_singles_become_single_batches():
    events = _events([_msg(1), _msg(2)])
    assert _shape(events) == [("Batch", [1]), ("Batch", [2])]


def test_service_message_yields_skip():
    events = _events([_msg(1), _msg(2, action="join"), _msg(3)])
    assert _shape(events) == [("Batch", [1]), ("ServiceSkip", 2), ("Batch", [3])]


def test_album_accumulates_and_flushes_on_single():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=7), _msg(3)])
    assert _shape(events) == [("Batch", [1, 2]), ("Batch", [3])]


def test_album_flushes_on_group_change():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=8)])
    assert _shape(events) == [("Batch", [1]), ("Batch", [2])]


def test_album_flushes_on_service_message():
    events = _events([_msg(1, grouped_id=7), _msg(2, action="pin")])
    assert _shape(events) == [("Batch", [1]), ("ServiceSkip", 2)]


def test_trailing_album_flushes_at_stream_end():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=7)])
    assert _shape(events) == [("Batch", [1, 2])]


def test_empty_stream_yields_nothing():
    assert _events([]) == []


@pytest.mark.parametrize("bad", [True, "7", 1.5])
def test_invalid_grouped_id_raises_policy_error(bad):
    with pytest.raises(PolicyError, match="album group id is invalid"):
        _events([_msg(1, grouped_id=bad)])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_clone_batching.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.clone.batching'`

- [x] **Step 3: Write the implementation**

```python
# src/tgcli/clone/batching.py
"""Pure batch planning for clone sync: albums and service skips, no Telethon."""

from dataclasses import dataclass

from tgcli.errors import PolicyError


@dataclass(frozen=True)
class ServiceSkip:
    message_id: int


@dataclass(frozen=True)
class Batch:
    messages: tuple


def _group_id(message):
    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id is None:
        return None
    if isinstance(grouped_id, bool) or not isinstance(grouped_id, int):
        raise PolicyError("clone album group id is invalid")
    return grouped_id


async def plan(messages):
    """Yield ServiceSkip and Batch events from an async message stream."""
    album: list = []
    async for message in messages:
        if getattr(message, "action", None) is not None:
            if album:
                yield Batch(tuple(album))
                album = []
            yield ServiceSkip(message.id)
            continue
        grouped_id = _group_id(message)
        if grouped_id is not None:
            if album and album[0].grouped_id == grouped_id:
                album.append(message)
                continue
            if album:
                yield Batch(tuple(album))
            album = [message]
            continue
        if album:
            yield Batch(tuple(album))
            album = []
        yield Batch((message,))
    if album:
        yield Batch(tuple(album))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_clone_batching.py -q`
Expected: PASS (8 tests)

- [x] **Step 5: Full suite + commit**

Run: `pytest -q`
Expected: PASS

```bash
git add src/tgcli/clone/batching.py tests/test_clone_batching.py
git commit -m "Add pure clone batch planner"
```

---

### Task 2: Rewire `sync_text` onto `batching.plan`

**Files:**
- Modify: `src/tgcli/commands/clone.py` (the `sync_text` loop; at snippet base
  62866d3 this is lines 308–373)

**Interfaces:**
- Consumes: `batching.plan`, `batching.Batch`, `batching.ServiceSkip` (Task 1).
- Produces: `sync_text` with identical external behavior; exactly **one**
  `limit` check; the `active_album` state machine and all three duplicated
  `limit` checks deleted.

- [x] **Step 1: Confirm the acceptance net is green before touching the loop**

Run: `pytest tests/test_cli_clone_sync.py -q`
Expected: PASS. These tests are the spec for this task — they must pass
after the rewire **without modification**.

- [x] **Step 2: Replace the loop**

Add `batching` to the clone package import in `src/tgcli/commands/clone.py`:

```python
from tgcli.clone import attribution, batching, fidelity, profile, replies, state
```

Replace everything from `copied = 0` down to the trailing
`if active_album: await finish_batch(active_album)` (inclusive) with:

```python
    copied = 0
    copied_batches = 0
    skipped_service = 0
    skipped_unsupported = []
    transport_counts = {"forwarded": 0, "reuploaded": 0, "snapshots": 0}
    reply_flattened = 0
    author_cache = {}
    more = False

    async def copy_batch(messages) -> None:
        nonlocal copied, copied_batches, reply_flattened
        unsupported = [
            {"id": message.id, "kind": kind}
            for message in messages
            if (kind := fidelity.unsupported_kind(message)) is not None
        ]
        if unsupported:
            skipped_unsupported.extend(unsupported)
            clone_state.cursor = messages[-1].id
            state.save(clone_state)
            return
        batch_copied, mode, flattened = await _forward_batch(
            tg, source_entity, destination, clone_state, account_alias,
            list(messages), me, author_cache
        )
        copied += batch_copied
        transport_counts[mode] += batch_copied
        reply_flattened += int(flattened)
        copied_batches += 1

    async for event in batching.plan(tg.iter_messages(
            source_entity, min_id=clone_state.cursor, reverse=True)):
        if limit is not None and copied_batches >= limit:
            more = True
            break
        if isinstance(event, batching.ServiceSkip):
            skipped_service += 1
            clone_state.cursor = event.message_id
            state.save(clone_state)
            continue
        await copy_batch(event.messages)
```

Keep the code after the loop (`clone_state.last_synced_at = ...` onward)
unchanged. Note the equivalence argument for reviewers: the original checked
`limit` at three boundaries but `copied_batches` can only change inside
`finish_batch`, so one check before consuming the next event is equivalent —
including the trailing-album case, where the count cannot have changed since
the album's first item passed the check.

- [x] **Step 3: Run the acceptance net**

Run: `pytest tests/test_cli_clone_sync.py -q`
Expected: PASS with zero test-file changes. If any test fails, the rewire
changed behavior — fix the rewire, never the test.

- [x] **Step 4: Full suite + commit**

Run: `pytest -q`
Expected: PASS

```bash
git add src/tgcli/commands/clone.py
git commit -m "Drive clone sync from the batch planner"
```

---

### Task 3: `clone/transport.py` — pure transport decision

**Files:**
- Create: `src/tgcli/clone/transport.py`
- Test: `tests/test_clone_transport.py`

**Interfaces:**
- Consumes: `replies.target(messages, clone_state, source)`,
  `fidelity.supports(message)` (both existing).
- Produces:
  - `@dataclass(frozen=True) TransportPlan(mode: str, reply_to: object | None,
    reply_flattened: bool, needs_author: bool)` with
    `mode ∈ {"forwarded", "reuploaded", "snapshots"}`
  - `def decide(messages, clone_state, source) -> TransportPlan` — pure, no
    awaits, no network.

Decision contract (copied from `_forward_batch` at 62866d3, lines 241–250):
- `reply_to = replies.target(messages, clone_state, source)`
- `reply_flattened` = first message has `reply_to` metadata but the mapped
  target is `None`
- `mode = "snapshots"` iff the batch is a single message and
  `fidelity.supports(message)` (poll or story — `fidelity.replacement`
  returns non-`None` for exactly these, so the pure predicate is equivalent)
- else `mode = "reuploaded"` iff source has `noforwards`, or any message has
  `noforwards`, or `reply_to is not None`
- else `mode = "forwarded"`
- `needs_author` = `clone_state.source_kind != "broadcast"` and
  `mode != "forwarded"` (equivalent to the original
  `replacement is not None or reupload`)

- [x] **Step 1: Write the failing tests**

```python
# tests/test_clone_transport.py
"""Unit tests for the pure clone transport decision."""
from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import state, transport


def _clone_state(kind="broadcast", id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src",
        source_kind=kind)
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    return clone_state


def _source(noforwards=False):
    return SimpleNamespace(id=2, noforwards=noforwards)


def _msg(message_id=10, *, reply_to=None, media=None, noforwards=False):
    return SimpleNamespace(id=message_id, reply_to=reply_to, media=media,
                           noforwards=noforwards)


def test_plain_broadcast_message_is_forwarded():
    plan = transport.decide([_msg()], _clone_state(), _source())
    assert plan.mode == "forwarded"
    assert plan.reply_to is None
    assert plan.reply_flattened is False
    assert plan.needs_author is False


def test_protected_source_forces_reupload():
    plan = transport.decide([_msg()], _clone_state(), _source(noforwards=True))
    assert plan.mode == "reuploaded"


def test_protected_message_forces_reupload():
    plan = transport.decide(
        [_msg(noforwards=True)], _clone_state(), _source())
    assert plan.mode == "reuploaded"


def test_mapped_reply_forces_reupload_with_target():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    plan = transport.decide(
        [_msg(reply_to=header)], _clone_state(id_map={5: 105}), _source())
    assert plan.mode == "reuploaded"
    assert isinstance(plan.reply_to, types.InputReplyToMessage)
    assert plan.reply_to.reply_to_msg_id == 105
    assert plan.reply_flattened is False


def test_unmapped_reply_is_flattened_and_forwarded():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    plan = transport.decide(
        [_msg(reply_to=header)], _clone_state(), _source())
    assert plan.mode == "forwarded"
    assert plan.reply_to is None
    assert plan.reply_flattened is True


def test_single_poll_becomes_snapshot():
    media = types.MessageMediaPoll(poll=None, results=None)
    plan = transport.decide([_msg(media=media)], _clone_state(), _source())
    assert plan.mode == "snapshots"


def test_album_never_snapshots():
    media = types.MessageMediaPoll(poll=None, results=None)
    plan = transport.decide(
        [_msg(1, media=media), _msg(2, media=media)],
        _clone_state(), _source())
    assert plan.mode == "forwarded"


def test_megagroup_needs_author_when_not_forwarded():
    plan = transport.decide(
        [_msg()], _clone_state(kind="megagroup"), _source(noforwards=True))
    assert plan.needs_author is True


def test_megagroup_forward_needs_no_author():
    plan = transport.decide(
        [_msg()], _clone_state(kind="megagroup"), _source())
    assert plan.needs_author is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_clone_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.clone.transport'`

- [x] **Step 3: Write the implementation**

```python
# src/tgcli/clone/transport.py
"""Decide how a clone batch travels: forward, reupload, or snapshot."""

from dataclasses import dataclass

from tgcli.clone import fidelity, replies


@dataclass(frozen=True)
class TransportPlan:
    mode: str
    reply_to: object | None
    reply_flattened: bool
    needs_author: bool


def decide(messages, clone_state, source) -> TransportPlan:
    reply_to = replies.target(messages, clone_state, source)
    reply_flattened = (getattr(messages[0], "reply_to", None) is not None
                       and reply_to is None)
    if len(messages) == 1 and fidelity.supports(messages[0]):
        mode = "snapshots"
    elif (getattr(source, "noforwards", False) or reply_to is not None
            or any(getattr(message, "noforwards", False)
                   for message in messages)):
        mode = "reuploaded"
    else:
        mode = "forwarded"
    needs_author = (clone_state.source_kind != "broadcast"
                    and mode != "forwarded")
    return TransportPlan(mode=mode, reply_to=reply_to,
                         reply_flattened=reply_flattened,
                         needs_author=needs_author)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_clone_transport.py -q`
Expected: PASS (9 tests)

- [x] **Step 5: Full suite + commit**

Run: `pytest -q`
Expected: PASS

```bash
git add src/tgcli/clone/transport.py tests/test_clone_transport.py
git commit -m "Add pure clone transport decision"
```

---

### Task 4: Split rendering out of `fidelity.py` into `clone/snapshot.py`

**Files:**
- Create: `src/tgcli/clone/snapshot.py`
- Modify: `src/tgcli/clone/fidelity.py` (delete moved code)
- Test: `tests/test_clone_snapshot.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `snapshot.render(tg, message) -> tuple[str, list]` — the old
    `fidelity.replacement` minus the `None` path: caller guarantees the
    message is a supported snapshot (poll or story); raises `AssertionError`
    otherwise (programming error, not user input).
  - `fidelity.py` keeps only `supports` / `unsupported_kind` and
    `_NATIVE_MEDIA_TYPES` (capability classification).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_clone_snapshot.py
"""Unit tests for poll/story snapshot rendering."""
import asyncio
from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import snapshot


def test_bar_renders_eighth_blocks():
    assert snapshot._bar(0) == "░" * 10
    assert snapshot._bar(100) == "█" * 10
    assert snapshot._bar(50) == "█████" + "░" * 5


def test_vote_word_russian_plurals():
    assert snapshot._vote_word(1) == "голос"
    assert snapshot._vote_word(2) == "голоса"
    assert snapshot._vote_word(5) == "голосов"
    assert snapshot._vote_word(11) == "голосов"
    assert snapshot._vote_word(21) == "голос"


def test_poll_snapshot_text():
    media = SimpleNamespace(
        poll=SimpleNamespace(
            question=SimpleNamespace(text="Вопрос?"),
            answers=[
                SimpleNamespace(text=SimpleNamespace(text="Да"), option=b"0"),
                SimpleNamespace(text=SimpleNamespace(text="Нет"), option=b"1"),
            ],
        ),
        results=SimpleNamespace(
            total_voters=4,
            results=[
                SimpleNamespace(option=b"0", voters=3),
                SimpleNamespace(option=b"1", voters=1),
            ],
        ),
    )
    text, entities = snapshot._poll_snapshot(media)
    assert "📊 Результаты опроса" in text
    assert "Вопрос?" in text
    assert "75%" in text and "3 голоса" in text
    assert "Проголосовало: 4" in text
    assert entities == []


def test_story_render_links_known_username():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(title=None, first_name="Ann",
                                   last_name=None, username="ann")

    media = types.MessageMediaStory(peer=types.PeerUser(user_id=7), id=3)
    message = SimpleNamespace(media=media)
    text, entities = asyncio.run(snapshot.render(Client(), message))
    assert text == "Stories недоступна\nАвтор: Ann"
    assert len(entities) == 1
    assert entities[0].url == "https://t.me/ann"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_clone_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.clone.snapshot'`

- [x] **Step 3: Move the code**

Create `src/tgcli/clone/snapshot.py` with the module docstring
`"""Render truthful text snapshots for polls and stories (ADR-0019)."""` and
move these from `fidelity.py`, byte-for-byte except the rename of
`replacement` → `render`: `_vote_word`, `_bar`, `_poll_snapshot`,
`_utf16_length`, and `replacement`. In the moved `render`, replace the
`return None` fallthrough with an assertion, since callers now pre-check
`fidelity.supports`:

```python
async def render(tg, message) -> tuple[str, list]:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPoll):
        return _poll_snapshot(media)
    assert isinstance(media, types.MessageMediaStory), "render() requires fidelity.supports()"
    try:
        peer = await tg.get_entity(media.peer)
    except ValueError:
        peer = None
    title = getattr(peer, "title", None)
    name = " ".join(
        item for item in (
            getattr(peer, "first_name", None), getattr(peer, "last_name", None)
        ) if item
    )
    username = getattr(peer, "username", None)
    label = title or name or (f"@{username}" if username else "неизвестен")
    prefix = "Stories недоступна\nАвтор: "
    text = prefix + label
    entities = [types.MessageEntityTextUrl(
        offset=_utf16_length(prefix), length=_utf16_length(label),
        url=f"https://t.me/{username}",
    )] if username else []
    return text, entities
```

`fidelity.py` after the move contains only: module docstring (update to
`"""Classify which Telegram media a clone can carry natively."""`),
`_NATIVE_MEDIA_TYPES`, `supports`, `unsupported_kind`. Nothing in the repo
imports `fidelity.replacement` after Task 5 — but at this point
`commands/clone.py` still does, so temporarily keep a one-line alias at the
bottom of `fidelity.py`:

```python
from tgcli.clone.snapshot import render as replacement  # removed in Task 5
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_clone_snapshot.py -q && pytest -q`
Expected: PASS (new tests and full suite — the alias keeps clone.py working)

- [x] **Step 5: Commit**

```bash
git add src/tgcli/clone/snapshot.py src/tgcli/clone/fidelity.py tests/test_clone_snapshot.py
git commit -m "Split snapshot rendering out of fidelity classification"
```

---

### Task 5: Rewire `_forward_batch` onto `TransportPlan`

**Files:**
- Modify: `src/tgcli/commands/clone.py` (`_forward_batch`; at snippet base
  62866d3 lines 237–279)
- Modify: `src/tgcli/clone/fidelity.py` (drop the temporary alias)

**Interfaces:**
- Consumes: `transport.decide` (Task 3), `snapshot.render` (Task 4).
- Produces: `_forward_batch` as a thin executor: one `decide()` call, then a
  three-way dispatch to the existing adapters. No decision logic left inline.

- [x] **Step 1: Acceptance net green**

Run: `pytest tests/test_cli_clone_sync.py -q`
Expected: PASS (pre-change baseline).

- [x] **Step 2: Rewire**

Update the clone package import in `src/tgcli/commands/clone.py` (also remove
`fidelity` if the rewired file no longer references it — `copy_batch` in
`sync_text` still uses `fidelity.unsupported_kind`, so it stays):

```python
from tgcli.clone import (attribution, batching, fidelity, profile, replies,
                         snapshot, state, transport)
```

Replace `_forward_batch` with:

```python
async def _forward_batch(tg, source, destination, clone_state, account_alias,
                         messages, me, author_cache):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    plan = transport.decide(messages, clone_state, source)
    author = None
    if plan.needs_author:
        author = await attribution.author_name(
            tg, source, messages[0], me, author_cache,
            lambda awaitable: _with_cooldown(awaitable, clone_state))
    if plan.mode == "snapshots":
        rendered_text, rendered_entities = await snapshot.render(tg, messages[0])
        text, entities = attribution.prefixed(rendered_text, rendered_entities, author)
        safety.append_audit("clone-sync-snapshot", account_alias,
                            {"clone_id": clone_state.clone_id,
                             "source_message_ids": source_ids})
        response = await _mutate(tg, functions.messages.SendMessageRequest(
            peer=destination, message=text, random_id=random_ids[0],
            reply_to=plan.reply_to, no_webpage=True, entities=entities), clone_state)
    elif plan.mode == "forwarded":
        safety.append_audit("clone-sync-forward", account_alias,
                            {"clone_id": clone_state.clone_id,
                             "source_message_ids": source_ids})
        request = functions.messages.ForwardMessagesRequest(
            from_peer=source, id=source_ids, random_id=random_ids,
            to_peer=destination, drop_author=clone_state.source_kind == "broadcast",
        )
        response = await _mutate(tg, request, clone_state)
    else:
        response = await _reupload_batch(tg, destination, clone_state, account_alias,
                                         messages, random_ids, plan.reply_to, author)
    destination_ids = _confirmed_destination_ids(response, random_ids)
    for source_id, destination_id in zip(source_ids, destination_ids, strict=True):
        clone_state.record_mapping(source_id, destination_id)
    clone_state.cursor = source_ids[-1]
    state.save(clone_state)
    return len(source_ids), plan.mode, plan.reply_flattened
```

Behavior-order note: the original awaited `fidelity.replacement` (a network
call only for stories) **before** `author_name`; the rewire calls
`author_name` first and renders after. Both calls go through the same
cooldown wrapper and touch independent state, and no existing test encodes
the relative order — but verify this against the merged round-2 code in case
it added ordering-sensitive audit records between them.

Then delete the temporary alias line from `src/tgcli/clone/fidelity.py`.

- [x] **Step 3: Acceptance net + full suite**

Run: `pytest tests/test_cli_clone_sync.py -q && pytest -q`
Expected: PASS, zero test-file changes.

- [x] **Step 4: Commit**

```bash
git add src/tgcli/commands/clone.py src/tgcli/clone/fidelity.py
git commit -m "Execute clone batches from an explicit transport plan"
```

---

### Task 6: Inline `profile.py` into `commands/clone.py`

**Files:**
- Modify: `src/tgcli/commands/clone.py`
- Delete: `src/tgcli/clone/profile.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_copy_profile(tg, source, destination, account_alias, clone_id,
  cooldown)` — private phase of `commit_init`; `clone/profile.py` gone
  (deletion test: single caller, straight-line script, no complexity
  concentrated).

- [x] **Step 1: Move the function**

Copy the body of `profile.copy` into `src/tgcli/commands/clone.py` as
`_copy_profile` (same parameters, placed directly above `commit_init`), moving
the `functions`/`types` imports it needs (already imported in clone.py) and
keeping the audit calls byte-identical. Update the call site in `commit_init`:

```python
    await _copy_profile(tg, entity, destination, account_alias, clone_id,
                        lambda awaitable: _with_cooldown(awaitable, clone_state))
```

Remove `profile` from the clone package import. Delete
`src/tgcli/clone/profile.py`.

- [x] **Step 2: Full suite**

Run: `pytest -q`
Expected: PASS — `tests/test_cli_clone_init.py` covers profile copy through
the CLI; if any test imports `tgcli.clone.profile` directly, the run will
say so (none do at snippet base).

- [x] **Step 3: Commit**

```bash
git add -A src/tgcli/clone/profile.py src/tgcli/commands/clone.py
git commit -m "Inline profile copy into clone init"
```

---

### Task 7: Backfill direct unit tests for `attribution` and `replies`

**Files:**
- Test: `tests/test_clone_attribution.py` (create)
- Test: `tests/test_clone_replies.py` (create)

**Interfaces:**
- Consumes: existing `attribution.prefixed`, `attribution.author_name`,
  `replies.target` — no production changes in this task. If round 2 or the
  merged reality changed these signatures, mirror the merged code, not this
  snippet.

- [x] **Step 1: Write the tests (they should pass immediately)**

```python
# tests/test_clone_attribution.py
"""Direct unit tests for clone author attribution."""
import asyncio
from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import attribution


def test_prefixed_without_author_keeps_text_and_entities():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed("жирный", [entity], None)
    assert text == "жирный"
    assert entities == [entity]


def test_prefixed_shifts_entity_offsets_by_utf16_prefix():
    entity = types.MessageEntityBold(offset=0, length=4)
    text, entities = attribution.prefixed("текст", [entity], "Иван")
    assert text == "Иван: текст"
    assert entities[0].offset == 6  # len("Иван: ") in UTF-16 units
    assert entities[0] is not entity  # original must not be mutated


def test_prefixed_handles_surrogate_pair_author():
    text, entities = attribution.prefixed("hi", None, "😀")
    assert text == "😀: hi"
    assert entities is None


def test_author_name_uses_me_for_own_messages():
    me = SimpleNamespace(id=1, first_name="Me", last_name=None)
    message = SimpleNamespace(from_id=types.PeerUser(user_id=1),
                              sender_id=1, out=True)
    name = asyncio.run(attribution.author_name(
        None, SimpleNamespace(id=2), message, me, {}, None))
    assert name == "Me"


def test_author_name_caches_entity_lookups():
    calls = []

    class Client:
        async def get_entity(self, peer):
            calls.append(peer)
            return SimpleNamespace(title="Chan")

    async def cooldown(awaitable):
        return await awaitable

    cache = {}
    me = SimpleNamespace(id=1)
    message = SimpleNamespace(from_id=types.PeerChannel(channel_id=9),
                              sender_id=9, out=False)
    source = SimpleNamespace(id=2)
    first = asyncio.run(attribution.author_name(
        Client(), source, message, me, cache, cooldown))
    second = asyncio.run(attribution.author_name(
        Client(), source, message, me, cache, cooldown))
    assert first == second == "Chan"
    assert len(calls) == 1


def test_author_name_falls_back_to_id_for_unresolvable_peer():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(awaitable):
        return await awaitable

    me = SimpleNamespace(id=1)
    message = SimpleNamespace(from_id=types.PeerUser(user_id=9),
                              sender_id=9, out=False)
    name = asyncio.run(attribution.author_name(
        Client(), SimpleNamespace(id=2), message, me, {}, cooldown))
    assert name == "id 9"
```

```python
# tests/test_clone_replies.py
"""Direct unit tests for clone reply mapping."""
from types import SimpleNamespace

import pytest

from telethon.tl import types

from tgcli.clone import replies, state
from tgcli.errors import PolicyError


def _clone_state(id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src")
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    return clone_state


def _msg(reply_to=None):
    return SimpleNamespace(reply_to=reply_to)


SOURCE = SimpleNamespace(id=2)


def test_no_reply_metadata_returns_none():
    assert replies.target([_msg()], _clone_state(), SOURCE) is None


def test_mapped_reply_returns_destination_target():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    target = replies.target([_msg(header)], _clone_state({5: 105}), SOURCE)
    assert target.reply_to_msg_id == 105
    assert target.top_msg_id is None


def test_unmapped_parent_flattens_to_none():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    assert replies.target([_msg(header)], _clone_state(), SOURCE) is None


def test_story_reply_flattens_to_none():
    header = types.MessageReplyStoryHeader(
        peer=types.PeerUser(user_id=7), story_id=3)
    assert replies.target([_msg(header)], _clone_state(), SOURCE) is None


def test_cross_peer_reply_is_rejected():
    header = types.MessageReplyHeader(
        reply_to_msg_id=5, reply_to_peer_id=types.PeerChannel(channel_id=999))
    with pytest.raises(PolicyError, match="cross-peer"):
        replies.target([_msg(header)], _clone_state(), SOURCE)


def test_forum_topic_reply_shape_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
    with pytest.raises(PolicyError, match="reply shape"):
        replies.target([_msg(header)], _clone_state(), SOURCE)


def test_album_reply_after_leading_item_is_rejected():
    header = types.MessageReplyHeader(reply_to_msg_id=5)
    with pytest.raises(PolicyError, match="leading item"):
        replies.target([_msg(), _msg(header)], _clone_state(), SOURCE)
```

Round-2 drift note: if the merged code changed reply flattening for forum
topics (round-2 tasks 6–7 route topics through `reply_to_top_id`), the
`forum_topic` rejection test above may contradict merged behavior — mirror
the merged code's contract and keep the rest.

- [x] **Step 2: Run the new tests**

Run: `pytest tests/test_clone_attribution.py tests/test_clone_replies.py -q`
Expected: PASS. A failure here means the test encodes the contract wrong —
read the production function and fix the test (production is frozen in this
task).

- [x] **Step 3: Full suite + commit**

Run: `pytest -q`
Expected: PASS

```bash
git add tests/test_clone_attribution.py tests/test_clone_replies.py
git commit -m "Backfill direct unit tests for clone attribution and replies"
```

---

### Task 8: Docs — MAP, comments-spec layout note, DEVLOG

**Files:**
- Modify: `docs/MAP.md`
- Modify: `docs/superpowers/specs/2026-07-16-clone-comments-design.md`
- Modify: `docs/DEVLOG.md`

- [x] **Step 1: Update MAP.md clone rows**

In the `src/tgcli/clone/` block: add rows for `batching.py`
(`pure batch planner: albums, service skips`), `transport.py`
(`pure forward/reupload/snapshot decision`), `snapshot.py`
(`truthful poll/story text rendering`); change the `fidelity.py` note to
`media capability classification`; delete the `profile.py` row.

- [x] **Step 2: Add a layout note to the comments spec**

At the top of the "module layout & budgets" section of
`docs/superpowers/specs/2026-07-16-clone-comments-design.md`, add:

```markdown
> Layout note (2026-07-16 deepening refactor): the phase-1 batch machinery is
> now `clone/batching.plan` (pure event generator) + `clone/transport.decide`
> (pure TransportPlan). The phase-2 discussion loop should consume these
> interfaces instead of duplicating the sync loop; file budgets in this
> section predate the refactor.
```

- [x] **Step 3: DEVLOG entry**

Prepend a DEVLOG entry using the repo template: Did (modules created/moved,
tests added, suites green), Decided (deepening before round 3; reference this
plan file), Learned (anything found during Task 0 drift review), Next
(implement clone comments round 3 on top of the new interfaces).

- [x] **Step 4: Commit**

```bash
git add docs/MAP.md docs/superpowers/specs/2026-07-16-clone-comments-design.md docs/DEVLOG.md
git commit -m "Document clone deepening refactor"
```

---

## Acceptance

- `pytest -q` green; `tests/test_cli_clone_*.py` unmodified throughout
  (except where a task explicitly says otherwise — none do at snippet base).
- Live visual gate (user rule: visual acceptance over test counts): run one
  real `tg clone sync <source> --limit 2` against the demo channel used in
  earlier clone acceptance, confirm the destination renders identically to a
  pre-refactor sync (order, prefixes, reply threading, poll snapshot).
- `git grep -n "copied_batches >= limit" src/` returns exactly one hit.

## Out of scope

- Candidate №3 (CLI command-registration seam in `cli.py`) — separate plan,
  after clone comments land.
- Any behavior change, including fixing quirks discovered while reading the
  old code (file an ISSUES.md entry instead).
- `mirror_probe` archival — already done in the session that wrote this plan.
