# Clone attribution for reposted source posts — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. Scope is fixed by
> [ADR-0050](../../decisions/ADR-0050-clone-forward-attribution.md).
> Part A ships as its own tagged patch release (ADR-0038). **Part B is
> gated** — do not start it without an explicit owner go-ahead in the
> current session.

**Goal:** A cloned post that is itself a forward stops reading as an
original channel statement. It carries a truthful `Переслано от <label>`
line built only from what `fwd_from` actually asserts.

**Non-goals:** claiming "из обсуждения" (`saved_from_peer` is null on this
shape); re-linking an author who hid their account; any change to the
forward path, which already preserves native headers via `_drops_author`;
any JSON or exit-code change.

## Global constraints

- TDD; no network in tests; boundary tests assert exact Telethon request
  types and arguments (AGENTS.md).
- CONTRACT.md updates land in the same commit as the code.
- `scripts/gate.sh` before every commit; raise flagged ceilings to
  measured size only.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- No live Telegram call at any point in Part A.

## Part A — the truthful prefix (one PR, one patch release)

### A1. `Author` carries a lead-in

- [x] Test (`tests/test_clone_attribution.py`): `Author(text="Имя",
      lead="Переслано от ")` renders prefix `Переслано от Имя\n\n`, and a
      `MessageEntityMentionName` covers **only** the name — `offset ==
      utf16_len("Переслано от ")`, `length == utf16_len("Имя")`.
- [x] Test: existing speaker-label callers (`lead=""`) keep the current
      `f"{text}: \n\n"` shape byte for byte; every existing attribution
      and clone-sync test stays green unchanged.
- [x] Test: a lead-in with non-BMP characters shifts body entity offsets
      by UTF-16 length, not `len()`.
- [x] Implement: `lead: str = ""` on `attribution.Author`; `prefixed()`
      uses it for both the text and the mention offset. One rendering
      seam, no second prefix function.

### A2. The `fwd_from` label ladder

- [x] Tests (`tests/test_clone_attribution.py`), one per rung, asserting
      the resulting `Author`:
      - `fwd_from.from_id = PeerUser` → resolved name, `@username` when
        active, else `MessageEntityMentionName` — identical to
        `_identify`'s existing behaviour;
      - `fwd_from.from_id = PeerChannel` → channel title, no mention;
      - `fwd_from.from_name = "Кто-то"` (account hidden) → that string
        verbatim, `mention_user_id is None` — **assert no mention entity
        is produced**, this is the privacy rung;
      - `fwd_from.post_author = "Редакция"` → the signature;
      - all fields empty → `Author(text="", lead="Переслано")`, i.e. the
        bare word, no fabricated id;
      - `get_entity` raises `ValueError` → falls back to the next rung
        rather than failing the batch.
- [x] Test: the resolver reuses the `author_cache` — two posts forwarded
      from the same user issue exactly one `get_entity`.
- [x] Test: `FloodWaitError` from the resolve propagates (cooldown wrapper
      path), it is not swallowed into a bare label.
- [x] Implement: `attribution.forwarded_author_of(tg, message, cache,
      cooldown)` reading `message.fwd_from`. It is a sibling of
      `author_of`, not a branch inside it — `author_of` answers "who sent
      this", this answers "who is it from".

### A3. Wire it into the posts leg

- [x] Test (pure, `tests/test_clone_transport.py`): `decide()` sets
      `needs_author=True` for a broadcast leg when `messages[0].fwd_from`
      is set and mode is `reuploaded`; still `False` for a broadcast post
      without `fwd_from`; still `False` when mode is `forwarded` (the
      native header survives there — no double attribution).
- [x] Test: the same for `snapshots` mode (a forwarded poll/story post
      also loses its header).
- [x] Test: album — the leading message's `fwd_from` governs the batch;
      only the first item gets the prefix, matching the existing
      `index == 0` author rule in `_reupload_batch`.
- [x] Test (integration, `tests/test_cli_clone_sync.py`): protected
      source, post with `fwd_from = PeerUser` → exactly one
      `SendMediaRequest`/`SendMessageRequest` whose `message` starts with
      `Переслано от ` and whose `entities` carry the mention at the right
      offset; `sync.copied` unchanged; no new JSON field.
- [x] Test (regression): a protected post **without** `fwd_from` is byte
      identical to today's output.
- [x] Implement: thread the forwarded author through `_forward_batch` →
      `_reupload_batch` alongside the existing `author`, reusing
      `attribution.prefixed`. Keep `clone.py` growth minimal; put label
      construction in `attribution.py`.
- [x] CONTRACT §11 (clone): one sentence that a reposted post carries a
      `Переслано от` line on the reupload/snapshot paths, and that the
      clone never asserts a discussion-group origin it cannot prove.

### A4. Release

- [x] CHANGELOG + double version bump; MAP if a module gained a role;
      DEVLOG; PR → reviewer → CI → merge → tag.

## Part B — native re-forward of the proven original (GATED)

> Do not start without an explicit owner go-ahead. Costs one
> `messages.Search` per reposted post against the ADR-0045 flood budget,
> and its whole safety rests on the three-condition match.

- [x] Test: all three conditions hold (single candidate matching
      `fwd_from.from_id`, `fwd_from.date`, and the post's text/media) →
      exactly one `ForwardMessagesRequest` from the source group to the
      destination channel, and **no** `Переслано от` prefix (the native
      header carries it).
- [x] Test: the source group is `noforwards=true` → no search RPC at all,
      falls back to Part A.
- [x] Test: two candidates match sender+date → falls back to Part A; the
      clone never picks one.
- [x] Test: the candidate's text differs from the post (the post was
      edited after reposting — the live `[икона]` 69 case: reposted
      13:55, edited 16:18) → falls back to Part A. **This test is the
      point of Part B; without it the feature silently republishes
      different content.**
- [x] Test: the source group is unreachable (never joined, left) →
      falls back to Part A, no crash.
- [x] Implement behind the conditions, fallback-first: build the Part A
      prefix, then replace it with a forward only when the match is
      proven. `src/tgcli/clone/reforward.py`; audited as
      `clone-sync-reforward`. Narrowed to single-message `reuploaded`
      batches only: albums are excluded (every item would need its own
      proof) and snapshot-mode posts are excluded (their poll-vote
      replication, ADR-0048, is built on the rendered placeholder a
      forward would replace). A batch that takes this path is counted as
      `forwarded`, not `reuploaded`, in the sync JSON transport counts —
      no JSON field is added or removed.
- [x] CONTRACT + CHANGELOG + DEVLOG; separate PR, separate tag.
