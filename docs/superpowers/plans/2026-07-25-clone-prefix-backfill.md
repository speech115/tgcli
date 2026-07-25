# `tg clone refresh` — backfilling body prefixes into an already-copied clone — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`) syntax
> for tracking. Scope is fixed by
> [ADR-0054](../../decisions/ADR-0054-clone-refresh-body-prefix.md) (land it
> first — task 0). One PR, one tagged patch release (ADR-0038). Independent
> of [ADR-0051](../../decisions/ADR-0051-clone-windowed-phase-interleaving.md)
> — either may land first; `refresh` never touches `cursor`,
> `discussion_cursor`, or the interleaving loop.

**Goal:** a new `tg clone refresh SOURCE` subcommand that walks an existing
clone's `id_map`, finds posts whose body is still exactly the untouched
source text (no `Переслано от <label>` prefix ADR-0050 would add today), and
edits only those — so a clone copied before ADR-0050 (or before any future
prefix rule) can be made truthful without recopying.

**Non-goals:** re-rendering anything other than a missing author prefix (no
general "diff and fix" mode — the rejected ADR-0054 alternative); touching
media, `id_map`, `cursor`, or `discussion_cursor`; the discussion leg
(comments have always been attributed, ADR-0054 decision 3); a `--limit` or
batch-size flag (the candidate set is always small — bounded by how many
posts predate a given prefix rule); casting or retracting poll votes (that
is `clone sync`'s job via `snapshot.render`, never `refresh`'s).

## Global constraints

- TDD; no network in tests; boundary tests assert exact Telethon request
  types and arguments (AGENTS.md).
- `refresh` never calls `clone/snapshot.py`'s vote-casting path. The
  poll/story exclusion must be the *first* check on every candidate,
  before any attempt to compute a would-be render, so a poll can never
  reach a code path that talks to Telegram about its vote breakdown.
- CONTRACT.md updates land in the same commit as the code.
- `scripts/gate.sh` before every commit; raise flagged ceilings to measured
  size only.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- No live Telegram call at any point in this plan. Live acceptance on
  `[икона]` is owner-gated and happens after merge, if at all.

### 0. The ADR is already on this branch

- [x] `docs/decisions/ADR-0054-clone-prefix-backfill.md` and its index row
      in `docs/decisions/README.md` are the first commit of this branch.
      Nothing to do — the task is listed so the later tasks' citations
      resolve. ADR-0052, ADR-0053 and ADR-0055 are separate branches
      landing in parallel; the shared index table is where they conflict,
      and the resolution is always the union of the rows.

### 1. Pure eligibility predicate

The core rule (ADR-0054 decision 2) split into a synchronous, network-free
predicate plus a thin async renderer that supplies its inputs — so the rule
itself is testable with plain strings and entity lists, no fakes.

- [x] New module `src/tgcli/clone/refresh.py`.
- [x] Test (`tests/test_clone_refresh.py`): `eligible_for_backfill` — dest
      text/entities byte-identical to `message.message`/`message.entities`
      (no prefix at all) **and** the rendered-with-current-rules body
      differs from that raw source body → `True`.
- [x] Test, same function, each a separate case proving the *other* side of
      the discrimination:
      - dest text differs from the raw source body at all (already carries
        the ADR-0050 prefix, or the owner hand-edited it) → `False`. Cover
        both a text difference and an entities-only difference (same text,
        shifted/extra entity) — the "byte identical" requirement in the ADR
        covers both.
      - dest text matches the raw source body **and** the current renderer
        would *also* produce the raw body unchanged (no `fwd_from`, so
        `author` is `None`) → `False`. This is what makes a second `refresh`
        run on an already-fixed post a no-op instead of re-editing forever.
- [x] Implement `eligible_for_backfill(message, dest_text, dest_entities,
      rendered_text, rendered_entities) -> bool` in `clone/refresh.py`: two
      structural body comparisons (text equality plus an entity-list
      equality helper — compare via each entity's `to_dict()`, not object
      identity or `==`, the same "structural, not identity" concern
      `clone/reforward.py:_entities_key` already had to solve for a
      different comparison).
- [x] Test: async `render_with_current_rules(tg, message, cache, cooldown)`
      — with `message.fwd_from` set, calls `attribution.forwarded_author_of`
      exactly once (asserted on a fake) and returns
      `quote_fallback.apply_body(message, author, plan)`'s output, where
      `plan` is a throwaway `transport.TransportPlan(mode="reuploaded",
      reply_to=None, reply_flattened=False, needs_author=True)` — `reply_to`
      is irrelevant to body rendering (`apply_body` never reads it), so a
      fixed `None` is correct for every candidate regardless of the post's
      real reply relationship.
- [x] Test: same function with `message.fwd_from is None` never calls
      `forwarded_author_of` and returns the raw body unchanged (`author=None`
      through `attribution.prefixed` is already covered by
      `test_clone_attribution.py`; this test only proves `refresh` gates the
      call the same way `commands/clone.py:793-797` does at sync time — one
      seam, one rule, not reimplemented).
- [x] Implement `render_with_current_rules` in `clone/refresh.py`, reusing
      `quote_fallback.apply_body` and `attribution.forwarded_author_of`
      exactly as sync does — no new rendering logic (ADR-0054 decision 4).

### 2. The three exclusions, plus the album-leader guard

- [x] Test: a source message whose `media` is `MessageMediaPoll` (or
      `MessageMediaStory`) — `fidelity.supports(message)` — is excluded
      before `render_with_current_rules` is ever called (assert the fake
      `tg.get_entity`/vote RPCs are never invoked). Reason recorded:
      `"poll-snapshot"`.
- [x] Test: a destination message that already carries its own `fwd_from`
      (a genuine Telegram forward header — the ADR-0050 Part B native
      re-forward path) is excluded without calling
      `render_with_current_rules`. Reason: `"native-reforward"`.
- [x] Test: `clone_state.discussion_id_map` entries are never scanned even
      when a discussion-leg message would otherwise satisfy every other
      rule — the scan only ever iterates `clone_state.id_map`. (No
      per-message check needed; prove it by construction — a discussion-leg
      candidate handed to the scan directly is simply absent from either
      output list.)
- [x] Test: a grouped-media (album) message — `message.grouped_id is not
      None` — that is **not** the group's lead item (the item sync's
      `_reupload_batch` actually attached the author to, index 0 of the
      batch — i.e. the item with the lowest source id sharing that
      `grouped_id`) is excluded even though `eligible_for_backfill` alone
      would say yes for it in isolation. This guards a real correctness
      gap: only the lead item of an original album batch ever received a
      prefix (`commands/clone.py:733`, `author if index == 0 else None`),
      so treating a follower item as its own candidate would invent a
      prefix that was never supposed to exist there.
- [x] Test: the lead item of a grouped-media post is scanned normally
      (not excluded by the album guard).
- [x] Implement `candidates(tg, clone_state, source_entity,
      destination_entity, cooldown) -> tuple[list[Candidate],
      list[Excluded]]` in `clone/refresh.py`. Walks `clone_state.id_map`,
      batches `tg.get_messages(source, ids=[...])` and
      `tg.get_messages(destination, ids=[...])` (ADR-0054 consequence: "one
      `get_messages` per batch of mapped ids"), applies the gates in this
      order — `fwd_from is None` → not a candidate at all (silent, matches
      the sync-time gate); poll/story → excluded; native-reforward →
      excluded; album non-lead → excluded; otherwise render + test via task
      1's functions. `Candidate` carries `source_id`, `destination_id`,
      `text`, `entities`. `Excluded` carries `source_id`, `reason` (one of
      `"poll-snapshot"`, `"native-reforward"`, `"album-non-lead"`,
      `"not-eligible"` for anything that reaches
      `eligible_for_backfill` and gets `False` — hand-edited or
      already-fixed bodies included, reported but never alarmed on: this is
      the mechanism's normal steady state, not a failure).

### 3. `tg clone refresh SOURCE [--commit PREVIEW_ID]`: preview → commit

CLI grammar mirrors `clone init`, not `edit`/`send`: no separate `--preview`
flag — a bare `tg clone refresh SOURCE` *is* the preview, `--commit
PREVIEW_ID` is the commit. Preview consumption mirrors `clone init` too:
`safety.consume_preview` (single-shot, immediately burned), not
`safety.begin_commit` — a partial `refresh` commit is not meant to be
retried against the same preview id; recovery is a fresh `--preview` scan,
which quietly finds nothing for the posts already fixed (rule 2's own
idempotency does the recovery, not preview retry).

- [x] Test (`tests/test_cli_clone_refresh.py`): `clone refresh SOURCE`
      requires initialized clone state with a resolvable destination
      (mirrors `sync_text`'s guard) — same error family as
      `"clone is not initialized; run clone init first"` when absent.
- [x] Test: preview scans the account's per-clone cooldown
      (`_enforce_cooldown`) before any RPC and exits 5 if it is active — the
      scan is real network work (ADR-0054 consequence 4), unlike `clone
      init`'s lightweight preview, so it must not bypass the same cooldown
      gate `sync` and `init --commit` already respect.
- [x] Test: preview is unaffected by `--readonly` / `TGCLI_READONLY=1` /
      `TGCLI_NO_SEND=1` (read-only, same as `clone init` preview and
      `clone status`).
- [x] Test: preview JSON carries a `preview_id`, `expires_at`, the clone
      identity (`id`/`source`), and a `refresh` object with `eligible`
      (`[{source_id, destination_id}]`) and `excluded`
      (`[{source_id, reason}]`) lists. Preview payload persisted via
      `safety.create_preview` stores `kind: "clone-refresh"`, `source`,
      `account_user_id`, `source_peer_id`, and the `eligible` id pairs only
      — never the rendered text (recomputed fresh at commit, same
      staleness discipline as `clone-init`'s preview).
- [x] Test (`preflight.py`): `clone refresh SOURCE --commit PREVIEW_ID`
      blocked by `--readonly` / `TGCLI_READONLY=1` / `TGCLI_NO_SEND=1`
      before `safety.consume_preview` runs (mirrors the `clone init
      --commit` block at `preflight.py:173-180`).
- [x] Test: `--commit` whose preview `kind` isn't `"clone-refresh"`, or
      whose `source` doesn't match the `SOURCE` argument, is rejected
      (`PolicyError`) without touching Telegram — same shape as the
      `clone-init` check immediately above it in `preflight.py`.
- [x] Test: commit re-runs `eligible_for_backfill` against a **fresh**
      read of each candidate's destination text immediately before editing
      it. A candidate whose destination was already edited between preview
      and commit (by a previous partial run, or by hand) is skipped, not
      forced — this is rule 2 doing the idempotency work the ADR promises,
      exercised at the one point where it actually matters.
- [x] Test: each surviving edit writes a `clone-refresh-prefix` audit
      record (`safety.append_audit`) **before** its `EditMessageRequest`,
      carrying `clone_id`, `source_message_id`, `destination_message_id` —
      same "audit before RPC" discipline as `clone-sync-reupload` /
      `clone-sync-forward` / `clone-sync-snapshot`.
- [x] **Boundary test**: the mutation is exactly
      `functions.messages.EditMessageRequest(peer=<destination input peer>,
      id=<destination_message_id>, message=<new text>,
      entities=<new entities>)` — assert the recorded fake-`tg` call's type
      and every field. Assert explicitly that `media` is never set (default
      `None`, i.e. Telegram keeps the existing media untouched) and that no
      `SendMediaRequest`/`SendMessageRequest`/upload call happens at all —
      this is the ADR's "media is never touched and no message is
      recreated" rule (decision 5), made concrete as a request-shape
      assertion, not a comment.
- [x] Test: `MessageNotModifiedError` on an individual edit (Telegram's own
      idempotency signal, e.g. the same commit retried) is swallowed for
      that one post — same pattern as `mutate.commit_edit` — and the run
      continues to the next candidate rather than aborting.
- [x] Test: commit JSON reports `refresh: {edited: [...], skipped:
      [...], count}` (skipped reuses the preview's `excluded` shape plus
      any newly-stale candidate from the re-check above). Exit 0 whenever
      every candidate either got edited or was correctly declined — a
      rule-2 miss is not a failure (contrast with `sync`'s
      `quote_flattened` → exit 2; there is no equivalent "silent
      degradation" here to flag, only "the tool correctly refused to
      guess").
- [x] Implement `preview_refresh` / `commit_refresh` in
      `src/tgcli/commands/clone.py`, reusing `_enforce_cooldown`,
      `_with_cooldown`, `_mutate` exactly as `sync_text` does. Add
      `refresh_rows` for `--plain` output (columns: `source_id`,
      `destination_id`, `status` — `edited` or the exclusion reason).
- [x] Wire `parser.py`: `p_clone_refresh = clone_sub.add_parser("refresh",
      parents=[global_flags])`, `source` positional, `--commit
      metavar="PREVIEW_ID"` — no other flags.
- [x] Wire `preflight.py`: the `clone-refresh` commit-kind/source check
      alongside the existing `clone-init` block in `_prepare_previews`.
- [x] Wire `dispatch.py`: extend the `mutation_safe` computation (line
      26-29) with `or (args.clone_command == "refresh" and args.commit is
      not None)`, and add the `clone refresh` branch calling
      `clone_cmd.preview_refresh` / `clone_cmd.commit_refresh`.

### 4. FloodWait behaviour (inherited, not invented)

- [x] Test: a `FloodWaitError` raised from any RPC inside `commit_refresh`
      (scan re-check read, entity resolve, or the edit itself) arms
      `clone_state`'s cooldown and the account-scoped cooldown exactly like
      `sync_text` does, and the command exits 5. No retry loop anywhere in
      `refresh`.
- [x] Test: edits already applied before the FloodWait stay applied (no
      rollback — `EditMessageRequest` calls already confirmed by Telegram
      are not undone), and the next **fresh** `clone refresh SOURCE`
      preview (after the cooldown, no `--commit`) lists only the posts
      still missing their prefix — proves the "re-running refresh resumes
      naturally" consequence end-to-end (scan → cooldown-exit → fresh scan
      → shrunk candidate set), not just at the unit level.
- [x] CONTRACT §11 (new subsection, after the reply/quote paragraphs,
      before the reupload/transport paragraphs or wherever reads best next
      to the ADR-0050 prefix description it amends):
      - `tg clone refresh SOURCE` / `tg clone refresh SOURCE --commit
        PREVIEW_ID` grammar, preview JSON shape, commit JSON shape.
      - The eligibility rule in one sentence (byte-identical untouched body
        + current renderer would add a prefix), and the three exclusions
        by name.
      - `EditMessageRequest` text+entities only, media and `id_map`/cursors
        untouched.
      - `--readonly` / `TGCLI_READONLY` / `TGCLI_NO_SEND` gate the commit
        only, not the preview scan (cross-reference `clone init`'s
        identical rule).
      - FloodWait during preview or commit exits 5 through the same
        per-clone/account cooldown as `sync` and `init --commit`; note that
        the recovery path is a fresh preview, not a retried `--commit` of
        the same preview id (contrast this explicitly with `send`/`edit`'s
        `begin_commit` retry idiom, since a reader who knows that idiom
        will otherwise assume it applies here too).
      - Note ADR-0054 amends nothing already in CONTRACT — it adds a new,
        separate mutation path onto the *existing* ADR-0050 prefix rule.

### 5. Documentation and release

- [x] `docs/MAP.md`: add a `refresh.py` row under `clone/` (next to
      `reforward.py`, same indentation/column style) describing it as "body
      backfill: eligibility + candidate scan for `tg clone refresh`
      (ADR-0054)", and append `/0054` to the `clone/` directory line's ADR
      parenthetical.
- [x] `docs/guide/clone.md`: new section after the `sync` walkthrough
      documenting `tg clone refresh SOURCE`, when to use it (a clone copied
      before a prefix rule shipped), and that it is read-then-edit only —
      never recreates or reorders anything.
- [x] `SKILL.md`: add a row to the clone command table (`Backfill missing
      forward prefixes on an existing clone | tg --json clone refresh
      SOURCE`), next to the existing `init`/`sync` rows.
- [x] CHANGELOG.md entry naming ADR-0054; bump `pyproject.toml` and
      `src/tgcli/__init__.py` patch version together (1.2.10 → 1.2.11) in
      the same commit as the CONTRACT/CHANGELOG change (ADR-0038).
- [x] DEVLOG.md entry for the session (per AGENTS.md template).
- [ ] PR → `reviewer` subagent (independent whole-diff review against
      ADR-0054, this plan, and AGENTS.md — spec axis and standards axis
      both) → green CI on the PR-event run → merge → tag `v1.2.11` per
      `docs/agents/release.md`. Delete the branch as part of the merge.

### 6. Live acceptance (owner-gated, after merge and tag)

- [ ] Run `tg --json clone refresh <[икона] source>` (no `--commit`) and
      confirm the `eligible` list is exactly source ids `54, 69, 73, 78,
      81` — no more, no fewer — and that every other mapped id in the
      clone appears in neither list (ordinary posts, not `excluded` either,
      since they never had `fwd_from`).
- [ ] Inspect the preview's `excluded` list, if non-empty, and confirm
      every reason matches what is actually true of that post (e.g. any
      poll among the 60 shows `"poll-snapshot"`, not something scanned by
      accident).
- [ ] Only on explicit owner go-ahead: `tg --json clone refresh
      <source> --commit PREVIEW_ID`. Confirm in the destination channel
      that posts 54/69/73/78/81 now show the `Переслано от <label>` prefix,
      that their media is unchanged (same file, same caption otherwise),
      and that Telegram's "edited" marker appears (expected — ADR-0054
      consequence, not a bug).
- [ ] Re-run `tg --json clone refresh <source>` (preview only) once more
      and confirm the `eligible` list is now empty — proves idempotency on
      the real clone, not just in tests.
- [ ] Stop on exit 5; never retry in a loop (ADR-0045). If FloodWait hits
      mid-commit, wait out `retry_after`, then re-run a fresh preview (not
      a `--commit` of the old, already-consumed preview id) and confirm it
      only lists whatever is still unfixed.
