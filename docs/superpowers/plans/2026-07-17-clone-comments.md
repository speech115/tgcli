# Clone — Channel Comments (round 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cloned broadcast channel whose source has a readable linked discussion group gets a real, working comment section — private owned discussion megagroup, linked before the first post is copied, threads attached to the right posts.

**Architecture:** Init grows a second peer (discussion megagroup) created and linked via `channels.SetDiscussionGroupRequest` strictly before phase 1 sends any post, so Telegram creates the per-post auto-forward anchors. Sync becomes two sequential phases over the same batch machinery (`clone/batching.plan` + `clone/transport.decide`), each phase driven by a *leg* — a small facade exposing `source_kind` / `destination_kind` / `dest_for` / `record_mapping` / `cursor` over either the post fields or the new discussion fields of `CloneState`. Phase 2 skips Telegram's own auto-forwards and remaps each comment's thread anchor: source anchor → source post (`fwd_from.saved_from_msg_id`) → destination post (`id_map`) → destination anchor (`messages.getDiscussionMessage`, cached per run).

**Tech Stack:** Python 3.12+, Telethon (raw `functions.*` / `types.*`), pytest, uv-managed venv.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-07-16-clone-comments-design.md`. Read it before Task 1.
- No daemons, no background processes. Session files and secrets never enter the repo.
- Detection reads **only** `ChannelFull.linked_chat_id`. `linked_monoforum_id` is NOT a comment section — never read it (live-proven, @groks).
- Auto-forward recognition rule (live-proven): anchor has `fwd_from.saved_from_peer == PeerChannel(<source channel>)` and `fwd_from.saved_from_msg_id == <source post id>`. This pair *is* the source-anchor→source-post mapping — never call `getDiscussionMessage` on the source side.
- Direct comments have `reply_to_top_id = None` and only `reply_to_msg_id = <anchor id>`. Thread-root detection must key off `reply_to_msg_id == known anchor`, never off `top_id` being present.
- `from_id = None` occurs on real comments in the wild. The attribution ladder's no-sender steps must work.
- Comments are automatic when the source has them. No opt-out flag, no `--no-comments`.
- Never act on the source side: no joining, no sending, no linking on the source.
- No state migration. Bump `state.VERSION` to `2`; old files are rejected by the existing version check with the existing message.
- `--limit N` counts batches across both phases; phase 1 runs to exhaustion first, then phase 2 spends the remaining budget (open question resolved: sequential, phase 1 first).
- Line budgets: `clone/discussion.py` ≤ 120, `clone/legs.py` ≤ 60 (new module, see Task 3 note), `clone/attribution.py` ≤ 110, `clone/state.py` ≤ 190, `commands/clone.py` ≤ 460. Exceeding a budget requires cutting before adding. Verify with `wc -l`.
- Test command: `pytest -q` from repo root. Every task ends green.
- Commit after every task. Conventional-commit subjects, English.

---

### Task 1: Attribution ladder — identify the author, not just name them

Standalone slice (spec decision 6): amends the ADR-0021 prefix format globally, for all clone kinds. No comments code yet.

**Files:**
- Modify: `src/tgcli/clone/attribution.py` (`author_name` → `author_of`, `prefixed`)
- Modify: `src/tgcli/commands/clone.py:253-259, 218-219, 237-238` (call sites)
- Test: `tests/test_clone_attribution.py`, `tests/test_cli_clone_sync.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Author:
      text: str                      # rendered before ": ", e.g. "Ivan (@ivan)" or "id 9"
      mention_user_id: int | None = None   # set → MessageEntityMentionName over text

  async def author_of(tg, source, message, me, cache: dict, cooldown) -> Author
  def prefixed(text: str, entities, author: Author | None) -> tuple[str, list | None]
  ```

Ladder, first hit wins (mirrors spec Goals):
1. entity resolves, has an active username → `Author(text=f"{name} (@{username})")`, no mention entity (`@username` self-links).
2. entity resolves, no username, entity is a `types.User` → `Author(text=name, mention_user_id=entity.id)`.
3. entity resolves, no username, entity is not a user (channel sender / anonymous admin) → `Author(text=name)` plain — `MentionName` takes a user id only.
4. entity unresolvable → `Author(text=f"id {sender_id}")`.
5. no sender at all → `Author(text=post_author)` if `post_author` is a non-empty string, else `Author(text="id unknown")`.

Username source: `entity.username` if truthy, else the first `usernames` item with `active` true. Nothing is fabricated.

`prefixed` keeps the existing UTF-16 offset shift for pre-existing entities and, when `mention_user_id` is set, prepends `types.MessageEntityMentionName(offset=0, length=<utf-16 length of author.text>, user_id=author.mention_user_id)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_clone_attribution.py` (keep the existing tests, adapting them to `Author`/`author_of`):

```python
def test_prefixed_with_username_author_adds_no_mention_entity():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="Ivan (@ivan)"))
    assert text == "Ivan (@ivan): hi"
    assert entities is None


def test_prefixed_mentions_author_without_username():
    text, entities = attribution.prefixed(
        "текст", None, attribution.Author(text="Иван", mention_user_id=7))
    assert text == "Иван: текст"
    assert entities == [types.MessageEntityMentionName(
        offset=0, length=4, user_id=7)]


def test_prefixed_mention_length_counts_utf16_units():
    text, entities = attribution.prefixed(
        "hi", None, attribution.Author(text="😀 Ann", mention_user_id=7))
    assert text == "😀 Ann: hi"
    assert entities[0].length == 6  # surrogate pair counts as 2


def test_prefixed_mention_coexists_with_shifted_entities():
    bold = types.MessageEntityBold(offset=0, length=2)
    text, entities = attribution.prefixed(
        "hi", [bold], attribution.Author(text="Ann", mention_user_id=7))
    assert text == "Ann: hi"
    assert entities == [types.MessageEntityMentionName(offset=0, length=3, user_id=7),
                        types.MessageEntityBold(offset=5, length=2)]
    assert bold.offset == 0  # original must not be mutated


def test_author_of_prefers_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username="ivan")

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Ivan (@ivan)")


def test_author_of_mentions_user_without_username():
    class Client:
        async def get_entity(self, peer):
            return types.User(id=9, first_name="Ivan", username=None)

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="Ivan", mention_user_id=9)


def test_author_of_falls_back_to_id_for_unresolvable_peer():
    class Client:
        async def get_entity(self, peer):
            raise ValueError("no such peer")

    async def cooldown(awaitable):
        return await awaitable

    author = asyncio.run(attribution.author_of(
        Client(), SimpleNamespace(id=2),
        SimpleNamespace(from_id=types.PeerUser(user_id=9), sender_id=9, out=False),
        SimpleNamespace(id=1), {}, cooldown))
    assert author == attribution.Author(text="id 9")


def test_author_of_uses_post_author_when_sender_is_absent():
    author = asyncio.run(attribution.author_of(
        None, SimpleNamespace(id=2, __class__=SimpleNamespace),
        SimpleNamespace(from_id=None, sender_id=None, out=False,
                        post_author="Editor"),
        SimpleNamespace(id=1), {}, None))
    assert author == attribution.Author(text="Editor")


def test_author_of_falls_back_to_id_unknown_without_sender_or_signature():
    author = asyncio.run(attribution.author_of(
        None, SimpleNamespace(id=2, __class__=SimpleNamespace),
        SimpleNamespace(from_id=None, sender_id=None, out=False, post_author=None),
        SimpleNamespace(id=1), {}, None))
    assert author == attribution.Author(text="id unknown")
```

Note: the last two use a non-`types.User` source so the `peer is None and isinstance(source, types.User)` branch does not fire.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_clone_attribution.py`
Expected: FAIL — `AttributeError: module 'tgcli.clone.attribution' has no attribute 'Author'`.

- [ ] **Step 3: Implement the ladder**

Rewrite `author_name` as `author_of` returning `Author`, keeping the existing me/source/cache branches. Extend `prefixed` to accept `Author | None` and emit `MessageEntityMentionName`. Keep the entity-copy discipline (never mutate caller entities).

- [ ] **Step 4: Update call sites**

In `src/tgcli/commands/clone.py`, `attribution.author_name(...)` → `attribution.author_of(...)`; the `author` value flows into `prefixed` unchanged (it is now an `Author`, not a `str`). Type hints on `_forward_batch`/`_reupload_batch` `author` params, if any, become `attribution.Author | None`.

- [ ] **Step 5: Fix the sync suite's prefix expectations**

`tests/test_cli_clone_sync.py` asserts prefixes like `"Ivan: text"` for megagroup clones. Update the affected assertions to the new ladder output for the fakes they use, and add one assertion that a username-less user sender produces a `MessageEntityMentionName` at offset 0 in the sent request's `entities`.

- [ ] **Step 6: Run the suite**

Run: `pytest -q`
Expected: PASS. Then `wc -l src/tgcli/clone/attribution.py` ≤ 110.

- [ ] **Step 7: Commit**

```bash
git add src/tgcli/clone/attribution.py src/tgcli/commands/clone.py tests/test_clone_attribution.py tests/test_cli_clone_sync.py
git commit -m "feat: identify clone authors by username or profile mention"
```

---

### Task 2: Discussion state fields

**Files:**
- Modify: `src/tgcli/clone/state.py`
- Test: `tests/test_clone_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces, on `CloneState` (flat fields, per spec §State):
  ```python
  VERSION = 2
  discussion_source_peer_id: int | None = None
  discussion_destination_peer_id: int | None = None
  discussion_linked: bool = False
  discussion_cursor: int = 0
  discussion_id_map: dict[str, int] = field(default_factory=dict)
  comments: str = "none"          # "enabled" | "unavailable" | "none"

  def record_discussion_mapping(self, source_id: int, destination_id: int) -> None
  def discussion_dest_for(self, source_id: int) -> int | None
  def max_discussion_destination_id(self) -> int | None
  ```
  `max_destination_id()` keeps its current meaning (posts + topics on the destination channel) and must NOT include discussion ids — they live on a different peer.

Validation in `from_dict`, fail-closed like `topic_map`: `comments` in the allowed set; `discussion_id_map` keys are canonical decimal strings in `1..2_147_483_647` with int values in the same range and no duplicate values; `discussion_cursor` a non-negative int; `comments == "enabled"` requires `discussion_source_peer_id` and `source_kind == "broadcast"`; `comments != "enabled"` forbids `discussion_id_map` and a non-zero `discussion_cursor`; `discussion_linked` implies `discussion_destination_peer_id is not None`. Anything else → `ValueError("inconsistent discussion state")`, which `load` already turns into the standard PolicyError.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_clone_state.py`, following the existing round-2 `topic_map` test style:

```python
def test_new_state_defaults_to_no_comments():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S")
    assert clone_state.comments == "none"
    assert clone_state.discussion_id_map == {}
    assert clone_state.discussion_cursor == 0
    assert clone_state.discussion_linked is False


def test_discussion_mapping_roundtrips(tmp_state_dir):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S")
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    clone_state.discussion_destination_peer_id = 66
    clone_state.discussion_linked = True
    clone_state.discussion_cursor = 9
    clone_state.record_discussion_mapping(3, 4)
    state.save(clone_state)
    loaded = state.load(clone_state.clone_id)
    assert loaded.discussion_dest_for(3) == 4
    assert loaded.discussion_cursor == 9
    assert loaded.comments == "enabled"


def test_max_destination_id_excludes_discussion_ids():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S")
    clone_state.record_mapping(1, 10)
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    clone_state.record_discussion_mapping(1, 900)
    assert clone_state.max_destination_id() == 10
    assert clone_state.max_discussion_destination_id() == 900


def test_load_rejects_version_1_state(tmp_state_dir):
    path = state.path_for("a" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "account_user_id": 1,
                                "source_peer_id": 2, "source_title": "S"}))
    with pytest.raises(PolicyError, match="unsupported version"):
        state.load("a" * 64)


def test_from_dict_rejects_unknown_comments_value():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(comments="maybe"))


def test_from_dict_rejects_enabled_comments_without_discussion_source():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(comments="enabled"))


def test_from_dict_rejects_discussion_map_without_enabled_comments():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(discussion_id_map={"1": 2}))


def test_from_dict_rejects_duplicate_discussion_destinations():
    with pytest.raises(ValueError):
        state.CloneState.from_dict(_valid_payload(
            comments="enabled", discussion_source_peer_id=55,
            discussion_id_map={"1": 2, "3": 2}))
```

Reuse the existing helper for a minimal valid payload if the file has one; otherwise add:

```python
def _valid_payload(**overrides):
    payload = {"version": state.VERSION, "account_user_id": 1, "source_peer_id": 2,
               "source_title": "S", "source_kind": "broadcast",
               "destination_kind": "broadcast", "topic_map": {}}
    payload.update(overrides)
    return payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_clone_state.py`
Expected: FAIL — missing `comments` / `record_discussion_mapping`.

- [ ] **Step 3: Implement fields, methods, validation, `VERSION = 2`**

Add fields to the dataclass, to `to_dict`, and validate in `from_dict`. Add the three methods.

- [ ] **Step 4: Run the suite**

Run: `pytest -q`
Expected: PASS (existing state tests that pin `version: 1` payloads must be updated to `state.VERSION`). Then `wc -l src/tgcli/clone/state.py` ≤ 190.

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/clone/state.py tests/test_clone_state.py
git commit -m "feat: add discussion fields to clone state"
```

---

### Task 3: Legs — one batch path, two destinations

Pure refactor, no behavior change. This is the seam that lets phase 2 reuse phase 1's machinery instead of duplicating the sync loop (spec Module layout note).

**Deviation from the spec's budget table (intentional, record it in the ADR):** the spec put everything in `discussion.py`. A new `clone/legs.py` keeps `state.py` a data module and `discussion.py` under 120 lines.

**Files:**
- Create: `src/tgcli/clone/legs.py` (≤ 60 lines)
- Modify: `src/tgcli/commands/clone.py` (`_forward_batch`, `copy_batch`, `sync_text`), `src/tgcli/clone/replies.py`, `src/tgcli/clone/transport.py`
- Test: `tests/test_clone_legs.py` (new), `tests/test_clone_replies.py`, `tests/test_clone_transport.py`

**Interfaces:**
- Consumes: Task 2's discussion fields.
- Produces:
  ```python
  class Leg:
      """One source→destination leg of a clone: the view of CloneState that
      batching/transport/replies read. Cursor and id-map writes land on the
      underlying CloneState; callers still save the CloneState itself."""
      clone_state: state.CloneState
      source_kind: str          # drives transport rules and forward drop_author
      destination_kind: str     # "forum" only for the posts leg of a forum clone
      def dest_for(self, source_id: int) -> int | None
      def record_mapping(self, source_id: int, destination_id: int) -> None
      cursor: int               # property, read/write
      clone_id: str             # property, delegates

  def posts(clone_state) -> Leg      # source_kind/destination_kind/id_map/cursor
  def discussion(clone_state) -> Leg # source_kind="megagroup", destination_kind="megagroup",
                                     # discussion_id_map / discussion_cursor
  ```

`replies.target(messages, leg, source)` and `transport.decide(messages, leg, source)` take a `Leg` where they took a `CloneState` — they already only read `source_kind`, `destination_kind`, and `dest_for`. Rename their parameter to `leg`; no logic changes. `_forward_batch` and `copy_batch` in `clone.py` take both `clone_state` (for cooldown/save/audit) and `leg` (for `record_mapping`, `cursor`, `source_kind` in `drop_author`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_clone_legs.py`:

```python
"""Direct unit tests for clone legs."""
from tgcli.clone import legs, state


def _state():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S")
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    return clone_state


def test_posts_leg_reads_and_writes_post_fields():
    clone_state = _state()
    leg = legs.posts(clone_state)
    leg.record_mapping(1, 10)
    leg.cursor = 1
    assert leg.dest_for(1) == 10
    assert clone_state.id_map == {"1": 10}
    assert clone_state.cursor == 1
    assert leg.source_kind == "broadcast"
    assert leg.destination_kind == "broadcast"


def test_posts_leg_reports_forum_destination_kind():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S", source_kind="forum")
    assert legs.posts(clone_state).destination_kind == "forum"


def test_discussion_leg_reads_and_writes_discussion_fields():
    clone_state = _state()
    leg = legs.discussion(clone_state)
    leg.record_mapping(1, 10)
    leg.cursor = 1
    assert leg.dest_for(1) == 10
    assert clone_state.discussion_id_map == {"1": 10}
    assert clone_state.discussion_cursor == 1
    assert clone_state.id_map == {}
    assert clone_state.cursor == 0


def test_discussion_leg_applies_megagroup_rules():
    leg = legs.discussion(_state())
    assert leg.source_kind == "megagroup"
    assert leg.destination_kind == "megagroup"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_clone_legs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.clone.legs'`.

- [ ] **Step 3: Implement `legs.py`**

- [ ] **Step 4: Rewire phase 1 through `legs.posts`**

Thread a `leg = legs.posts(clone_state)` through `sync_text` → `copy_batch` → `transport.decide` / `_forward_batch`. Replace `clone_state.record_mapping` / `clone_state.cursor` / `clone_state.source_kind` inside the batch path with the leg. `state.save(clone_state)` stays as-is. Update `tests/test_clone_replies.py` and `tests/test_clone_transport.py` to pass `legs.posts(clone_state)`.

- [ ] **Step 5: Run the suite**

Run: `pytest -q`
Expected: PASS with no test behavior changed — phase 1 is byte-for-byte the same behavior. Then `wc -l src/tgcli/clone/legs.py` ≤ 60.

- [ ] **Step 6: Commit**

```bash
git add src/tgcli/clone/legs.py src/tgcli/clone/replies.py src/tgcli/clone/transport.py src/tgcli/commands/clone.py tests/test_clone_legs.py tests/test_clone_replies.py tests/test_clone_transport.py
git commit -m "refactor: drive clone batches through an explicit leg"
```

---

### Task 4: `clone/discussion.py` — detection, anchors, auto-forwards

Pure-ish module: no sync loop, no init flow. Those land in Tasks 5 and 6.

**Files:**
- Create: `src/tgcli/clone/discussion.py` (≤ 120 lines)
- Test: `tests/test_clone_discussion.py` (new)

**Interfaces:**
- Consumes: `legs`, `state`, `topics.is_forum_destination`, `topics.create_request`.
- Produces:
  ```python
  def linked_chat_id(full_channel) -> int | None
      """ChannelFull.linked_chat_id only. linked_monoforum_id is a monoforum,
      not a comment section (live-proven on @groks) — never read it."""

  def is_discussion_destination(entity, *, title: str | None = None) -> bool
      """Private owned megagroup that is not a forum."""

  def autoforward_post_id(message, source_channel_id: int) -> int | None
      """Source post id if message is Telegram's auto-forward anchor for
      source_channel_id, else None. Matches fwd_from.saved_from_peer +
      saved_from_msg_id (live-proven)."""

  async def ensure_linked(mutate, channel, group) -> None
      """Idempotent: unhide pre-history, then SetDiscussionGroupRequest."""

  async def anchor_for(mutate, destination_channel, destination_post_id: int,
                       cache: dict) -> int | None
      """Destination anchor message id in the discussion group, via
      messages.getDiscussionMessage. Cached per run; None when Telegram
      returns no anchor."""
  ```

`autoforward_post_id` details: read `message.fwd_from`; require it to be a `types.MessageFwdHeader`; require `saved_from_peer` to be a `types.PeerChannel` with `channel_id == source_channel_id`; require `saved_from_msg_id` to be an int (not bool) in `1..2_147_483_647`; else `None`.

`ensure_linked` calls `functions.channels.TogglePreHistoryHiddenRequest(channel=group, enabled=False)` then `functions.channels.SetDiscussionGroupRequest(broadcast=channel, group=group)`. Both are idempotent server-side; a `telethon_errors.FloodWaitError` propagates so the caller's cooldown wrapper persists it.

`anchor_for` calls `functions.messages.GetDiscussionMessageRequest(peer=destination_channel, msg_id=destination_post_id)`; the anchor is the first item of `response.messages` with an int `id`; missing → `None`. Cache key is `destination_post_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clone_discussion.py`:

```python
"""Direct unit tests for clone discussion helpers."""
import asyncio
from types import SimpleNamespace

from telethon.tl import functions, types

from tgcli.clone import discussion


def _full(**overrides):
    values = {"linked_chat_id": None, "linked_monoforum_id": None}
    values.update(overrides)
    return SimpleNamespace(full_chat=SimpleNamespace(**values))


def test_linked_chat_id_reads_the_linked_chat():
    assert discussion.linked_chat_id(_full(linked_chat_id=55).full_chat) == 55


def test_linked_chat_id_ignores_monoforums():
    assert discussion.linked_chat_id(
        _full(linked_monoforum_id=77).full_chat) is None


def test_is_discussion_destination_accepts_private_owned_megagroup():
    entity = SimpleNamespace(title="G", creator=True, megagroup=True,
                             broadcast=False, forum=False, username=None,
                             usernames=[])
    assert discussion.is_discussion_destination(entity, title="G") is True


def test_is_discussion_destination_rejects_forum():
    entity = SimpleNamespace(title="G", creator=True, megagroup=True,
                             broadcast=False, forum=True, username=None,
                             usernames=[])
    assert discussion.is_discussion_destination(entity) is False


def _anchor(source_channel_id, post_id):
    return SimpleNamespace(id=1, fwd_from=types.MessageFwdHeader(
        date=None, channel_post=post_id,
        saved_from_peer=types.PeerChannel(channel_id=source_channel_id),
        saved_from_msg_id=post_id))


def test_autoforward_post_id_matches_saved_from_pair():
    assert discussion.autoforward_post_id(_anchor(123, 3680), 123) == 3680


def test_autoforward_post_id_rejects_other_channels():
    assert discussion.autoforward_post_id(_anchor(999, 3680), 123) is None


def test_autoforward_post_id_ignores_plain_messages():
    assert discussion.autoforward_post_id(
        SimpleNamespace(id=2, fwd_from=None), 123) is None


def test_ensure_linked_unhides_history_then_links():
    requests = []

    async def mutate(request):
        requests.append(request)

    asyncio.run(discussion.ensure_linked(mutate, "channel", "group"))
    assert isinstance(requests[0],
                      functions.channels.TogglePreHistoryHiddenRequest)
    assert requests[0].enabled is False
    assert isinstance(requests[1], functions.channels.SetDiscussionGroupRequest)
    assert requests[1].broadcast == "channel" and requests[1].group == "group"


def test_anchor_for_caches_lookups():
    calls = []

    async def mutate(request):
        calls.append(request.msg_id)
        return SimpleNamespace(messages=[SimpleNamespace(id=500)])

    cache = {}
    first = asyncio.run(discussion.anchor_for(mutate, "dest", 10, cache))
    second = asyncio.run(discussion.anchor_for(mutate, "dest", 10, cache))
    assert first == second == 500
    assert calls == [10]


def test_anchor_for_returns_none_without_anchor():
    async def mutate(request):
        return SimpleNamespace(messages=[])

    assert asyncio.run(discussion.anchor_for(mutate, "dest", 10, {})) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_clone_discussion.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.clone.discussion'`.

- [ ] **Step 3: Implement `discussion.py`**

- [ ] **Step 4: Run the suite**

Run: `pytest -q`
Expected: PASS. Then `wc -l src/tgcli/clone/discussion.py` ≤ 120.

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/clone/discussion.py tests/test_clone_discussion.py
git commit -m "feat: add clone discussion detection and anchor lookup"
```

---

### Task 5: Init — create and link the discussion group before the first post

**Files:**
- Modify: `src/tgcli/commands/clone.py` (`commit_init`, `_entry`, `init_rows` if needed)
- Test: `tests/test_cli_clone_init.py`, `tests/test_cli_clone_status.py`

**Interfaces:**
- Consumes: Task 2 state fields, Task 4 `discussion.*`.
- Produces: on a broadcast source with a readable linked group, a committed init leaves `comments == "enabled"`, `discussion_source_peer_id`, `discussion_destination_peer_id`, `discussion_linked == True` in state, and the `commit_init` payload gains `"comments": "enabled" | "unavailable" | "none"` under `clone`.

Flow inside `commit_init`, after the destination channel exists, titled, profiled, and **before returning** (nothing is synced during init, so "before the first post" is satisfied by any point in init):

1. Only for `source_kind == "broadcast"`. Any other kind → `comments = "none"`, nothing else.
2. `full = await _mutate(tg, functions.channels.GetFullChannelRequest(entity), clone_state)` — the existing `_copy_profile` already fetches this for channels; fetch once and reuse if that is a clean cut, otherwise a second call is acceptable.
3. `linked = discussion.linked_chat_id(full.full_chat)`; `None` → `comments = "none"`, save, return.
4. Readability probe: `await tg.get_entity(types.PeerChannel(linked))` then `await tg.get_messages(linked_entity, limit=1)`. A `ValueError`, `telethon_errors.ChannelPrivateError`, or `telethon_errors.ChatAdminRequiredError` → `comments = "unavailable"`, `discussion_source_peer_id = linked`, save, return posts-only. No error, no silent loss.
5. Readable → `comments = "enabled"`, `discussion_source_peer_id = linked`, save. Then create/adopt the group with the **same marker scheme as the channel**: marker `f"{marker}-discussion"`, `_marker_candidates(tg, discussion_marker, discussion.is_discussion_destination)`, same "matched multiple" / "wrong shape" PolicyErrors, `topics.create_request(discussion_marker)` to create. Audit `clone-init-discussion-create`. Save `discussion_destination_peer_id` immediately after creation — this is the crash-recovery point.
6. Title/about/avatar of the group copied from the source linked group, reusing `_copy_profile` and the existing `EditTitleRequest` step.
7. `await discussion.ensure_linked(lambda request: _mutate(tg, request, clone_state), destination, discussion_destination)`; then `clone_state.discussion_linked = True`; `state.save(clone_state)`. A crash between create and link leaves `discussion_linked == False` with a saved peer id; the next init re-runs `ensure_linked` (idempotent) and recovers. Audit `clone-init-discussion-link`.

Init now creates two peers per clone; the existing cooldown discipline (`_enforce_cooldown` / `_with_cooldown`) covers both, and recovered peers are reused via the marker scan — do not weaken either.

`_entry` (used by `clone status`) gains `"comments": s.comments`, and `status_rows` gains a trailing `comments` column.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_clone_init.py`, following the file's existing fake-client + `main([...])` style. Required cases:

```python
def test_init_creates_and_links_discussion_group(...):
    """Source with a readable linked group: init creates a private owned
    megagroup, links it via SetDiscussionGroupRequest, and records
    comments == "enabled" with discussion_linked True."""

def test_init_marks_unreadable_discussion_group_unavailable(...):
    """Linked group whose history raises ChannelPrivateError: init succeeds
    posts-only, state records comments == "unavailable" and the source
    discussion peer id, and no group is created."""

def test_init_without_linked_chat_records_no_comments(...):
    """linked_chat_id None: comments == "none", no extra requests."""

def test_init_ignores_monoforum_links(...):
    """linked_chat_id None + linked_monoforum_id set: comments == "none"."""

def test_init_relinks_after_a_crash_between_create_and_link(...):
    """Seeded state with discussion_destination_peer_id set and
    discussion_linked False: init adopts the peer (no CreateChannelRequest)
    and issues SetDiscussionGroupRequest, leaving discussion_linked True."""

def test_init_rejects_a_discussion_marker_matching_multiple_groups(...):
    """Two dialogs titled with the discussion marker: PolicyError, exit 4."""
```

The init fake client needs: `GetFullChannelRequest` returning a `full_chat` with `linked_chat_id`, `get_entity(PeerChannel(linked))` returning a megagroup, `get_messages(linked_entity, limit=1)` returning one message (or raising), `CreateChannelRequest` returning the new megagroup, and recording `TogglePreHistoryHiddenRequest` / `SetDiscussionGroupRequest`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_cli_clone_init.py -k discussion`
Expected: FAIL — no discussion requests issued.

- [ ] **Step 3: Implement the init flow**

- [ ] **Step 4: Add `comments` to status output**

Update `_entry`, `status_rows`, and `tests/test_cli_clone_status.py`.

- [ ] **Step 5: Run the suite**

Run: `pytest -q`
Expected: PASS. Then `wc -l src/tgcli/commands/clone.py` ≤ 460.

- [ ] **Step 6: Commit**

```bash
git add src/tgcli/commands/clone.py tests/test_cli_clone_init.py tests/test_cli_clone_status.py
git commit -m "feat: create and link a clone discussion group during init"
```

---

### Task 6: Sync phase 2 — clone the discussion group

**Files:**
- Modify: `src/tgcli/commands/clone.py` (`sync_text`, `_forward_batch`, `sync_rows`)
- Modify: `src/tgcli/clone/discussion.py` (only if the anchor remap needs a helper there)
- Test: `tests/test_cli_clone_sync.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: `sync` payload gains `"skipped_autoforward": int` and `"discussion_cursor": int`; `sync_rows` gains matching trailing columns.

Behavior:

- Phase 1 unchanged: posts, `legs.posts(clone_state)`, existing cursor, tail verification against the destination channel.
- Phase 2 runs only when `clone_state.comments == "enabled"`, after phase 1, over `tg.iter_messages(discussion_source, min_id=clone_state.discussion_cursor, reverse=True)` through the same `batching.plan` + `transport.decide` path with `legs.discussion(clone_state)`. A comment is never copied before its parent post because phase 1 runs to exhaustion first.
- Destination group resolution + shape check (`discussion.is_discussion_destination`) mirror the channel's, raising `PolicyError("clone discussion destination is not a private owned megagroup")` on mismatch.
- Tail verification on the discussion destination uses `max_discussion_destination_id()` as the baseline, and **must tolerate Telegram's own auto-forwards**: a tail message is expected if `discussion.autoforward_post_id(item, destination.id) is not None` (the anchor Telegram created for one of our posts) or it is a service message. Anything else → `PolicyError("clone discussion destination has unexpected tail messages; manual repair is required", unexpected=N)`.
- Auto-forward skip: for each phase-2 batch, if `discussion.autoforward_post_id(message, source_entity.id) is not None`, increment `skipped_autoforward`, advance `discussion_cursor`, save, continue. These anchors exist in the destination already — Telegram created them in phase 1.
- Anchor remap, per batch, before `transport.decide`:
  1. `header = messages[0].reply_to`; no header → plain megagroup message, nothing to remap.
  2. Thread root case: `parent = header.reply_to_msg_id`; if `parent` is a known source anchor (i.e. the source message with that id is an auto-forward — resolve via a per-run `source_anchor_posts: dict[int, int]` populated as phase 2 walks the auto-forwards, falling back to `tg.get_messages(discussion_source, ids=parent)` + `discussion.autoforward_post_id` when the anchor precedes the cursor), then: source post id → `clone_state.dest_for(post_id)` → destination post id → `discussion.anchor_for(mutate, destination, destination_post_id, anchor_cache)` → destination anchor id. Send as a reply to that anchor; Telegram renders it in the thread.
  3. Nested case (`header.reply_to_top_id` set): remap `reply_to_msg_id` through `legs.discussion(...).dest_for` (comment-on-comment) and `reply_to_top_id` through the anchor path in (2).
  4. Any unmappable parent flattens with the existing `reply_flattened` semantics — no fabrication.

  Keep this remap in one helper. `replies.target` stays untouched: it operates on the discussion leg's own id_map, and the anchor remap is layered on top by overriding `reply_to` on the resulting `types.InputReplyToMessage` (or constructing one when `replies.target` returned `None`).
- `--limit N`: `copied_batches` is shared across phases; phase 2 breaks on the same check and sets `more = True`. When the limit lands inside phase 2, comments lag posts until the next run — accepted.
- Per-batch guarantees carry over: state saved after each confirmed batch, FloodWait → cooldown persisted → exit 5 without advancing the current batch.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_clone_sync.py` a `CloneCommentsClient` (extends `CloneReuploadClient`): a source channel with a linked group, a seeded `comments="enabled"` state, `iter_messages` serving either peer, `GetDiscussionMessageRequest` returning an anchor id, and recording sends per peer. Required cases:

```python
def test_sync_copies_posts_before_comments(...):
    """Phase 1 sends every post before phase 2 sends any comment."""

def test_sync_skips_source_autoforwards(...):
    """Anchors in the source discussion group are not copied;
    sync["skipped_autoforward"] counts them."""

def test_sync_attaches_a_comment_to_its_post_thread(...):
    """Comment replying to the source anchor is sent into the destination
    group with reply_to.reply_to_msg_id == the destination anchor id from
    getDiscussionMessage(dest_channel, dest_post_id)."""

def test_sync_maps_comment_on_comment_replies(...):
    """Nested comment: reply_to_msg_id maps through discussion_id_map,
    top_msg_id through the anchor path."""

def test_sync_flattens_comments_with_unmapped_anchors(...):
    """Anchor that maps to no destination post: message is still copied,
    sync["reply_flattened"] == 1."""

def test_sync_copies_off_thread_group_messages(...):
    """A plain group message with no reply header clones as a plain
    megagroup message with an author prefix."""

def test_sync_advances_the_discussion_cursor_per_batch(...):
    """discussion_cursor is saved after each confirmed batch and a rerun
    copies nothing."""

def test_sync_limit_spends_phase_one_first(...):
    """--limit 1 with pending posts and comments: only a post batch is
    copied, more is True, discussion_cursor unchanged."""

def test_sync_tolerates_destination_autoforwards_in_the_tail(...):
    """Telegram's anchors in the destination group are not 'unexpected
    tail messages'."""

def test_sync_skips_phase_two_when_comments_are_unavailable(...):
    """comments == "unavailable": posts copy, no discussion requests,
    skipped_autoforward == 0."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_cli_clone_sync.py -k comment`
Expected: FAIL — `KeyError: 'skipped_autoforward'`.

- [ ] **Step 3: Implement phase 2**

- [ ] **Step 4: Run the suite**

Run: `pytest -q`
Expected: PASS. Then `wc -l src/tgcli/commands/clone.py` ≤ 460 — **if over, cut before adding** (candidates: move the tail verification and the anchor remap helper into `discussion.py`, which has room under its 120-line budget).

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/commands/clone.py src/tgcli/clone/discussion.py tests/test_cli_clone_sync.py
git commit -m "feat: clone channel comments into the linked discussion group"
```

---

### Task 7: Documentation

**Files:**
- Create: `docs/decisions/ADR-0023-clone-channel-comments.md`
- Modify: `docs/CONTRACT.md` (§11), `docs/MAP.md`, `docs/PLAN.md`, `docs/DEVLOG.md`
- Modify: `docs/superpowers/specs/2026-07-16-clone-comments-design.md` (Status → implemented; resolve the open question)

**Interfaces:** consumes the shipped behavior of Tasks 1–6.

- [ ] **Step 1: Write ADR-0023**

Follow the shape of `ADR-0022-clone-forum-topics.md`. Content: comments design; the anchor-timing constraint (link before the first post, no retroactive backfill, existing clones re-init to opt in); the global prefix-format amendment to ADR-0021 (Task 1's ladder); the `legs.py` deviation from the spec's module budget table and why; `comments: unavailable` as an honest permanent marker rather than a PolicyError; monoforum ≠ discussion group.

- [ ] **Step 2: Update CONTRACT.md §11**

Document: the `comments` field in `clone status` / `init` / `sync` output and its three values; the new `skipped_autoforward` and `discussion_cursor` sync counters; the new trailing plain-output columns for status/init/sync; the two-peer init and the `-discussion` marker; that `--limit` spends phase 1 first. Update the sample JSON lines to match the real payloads.

- [ ] **Step 3: Update MAP.md**

Add `clone/discussion.py` and `clone/legs.py` with one-line responsibilities.

- [ ] **Step 4: Update PLAN.md and the spec header**

Mark round 3 complete in PLAN.md. In the spec: `Status: implemented (2026-07-17)`, and under Open questions record the resolution — `--limit` runs phase 1 to exhaustion first.

- [ ] **Step 5: Append the DEVLOG entry**

Per AGENTS.md: what shipped, what is live-unverified (the live acceptance gate below), what is next.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: document clone channel comments"
```

---

## Live acceptance gate (user-run, after Task 7)

Mocked tests do not close this feature. Acceptance is visual (spec Testing):

1. `tg clone init <channel-with-active-comments>` on a real source; commit.
2. `tg clone sync <source>` to completion.
3. Verify in a client: the comments button appears under posts; thread contents and order match the source; author prefixes are clickable where the ladder promises (`@username` or profile mention).
4. Rerun `tg clone sync <source>` → `copied: 0`, idempotent.

Report FLOOD_WAIT behavior: init now creates two peers, and the live ~15 h flood wait on rapid peer creation applies double.
