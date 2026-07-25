# ADR-0055: pinned message carry-over + photo downscaling investigation — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`) syntax
> for tracking. Scope is fixed by
> [ADR-0055](../../decisions/ADR-0055-clone-pinned-and-photo-fidelity.md).
> The ADR file already exists, committed but unmerged, on branch
> `claude/clone-pinned-and-photo` (`docs/decisions/
> ADR-0055-clone-pinned-and-photo-fidelity.md` + its README index row, no
> code). Base your work branch on it — merge that branch first as a
> docs-only PR, or rebase your feature branch onto it, so the ADR lands in
> `main` no later than the code that implements it.
>
> This plan has **two independent tracks** that touch different code paths
> and can land as separate PRs in either order, each its own tagged patch
> release (ADR-0038): **Track A — pin carry-over** (Tasks 1–3) and
> **Track B — photo downscaling** (Tasks 4–6). Do not block one on the
> other.

**Goal:** Track A — a completing `clone sync` run mirrors the source's
pinned post onto the destination, silently and non-destructively. Track B —
establish, with evidence, why five audited photos came back smaller than
their source, fix the one confirmed latent defect (striped download trusting
`sizes[-1]`) regardless of what that evidence shows, and document whatever
the measurement proves about the audited five.

**Non-goals:**
- No CLI flag or config to opt out of pin carry-over (mirrors ADR-0055's
  decision framing: it is unconditional, silent, and additive).
- No mirroring of unpins or of a later source re-pin (ADR-0055 decisions
  2–3); once the clone has pinned something itself, it never touches the
  destination's pin again.
- No pin carry-over for forum-destination clones. ADR-0055's context is
  explicitly the broadcast "course channel" case, and Telegram's per-topic
  pin semantics for forum megagroups are a different, unaddressed shape.
  Gate the whole feature on `clone_state.destination_kind == "broadcast"`;
  a forum clone's sync JSON gets no `pinned` key at all, unchanged from
  today.
- No workaround for photo re-encoding (e.g., sending photos as documents).
  ADR-0055 decision 7 explicitly rejects this.
- No refactor of `_copy_profile`'s three-way `User`/`Chat`/`Channel`
  dispatch into a shared helper unless it falls out naturally while
  writing Task 1 — not a goal in itself.

## Global constraints

- TDD; no network in tests; boundary tests assert exact Telethon request
  types and arguments (AGENTS.md).
- CONTRACT.md updates land in the same commit as the code that changes
  behavior.
- `scripts/gate.sh` before every commit.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- Every new Telegram mutation gets a fail-closed `safety.append_audit` call
  before dispatch, matching every existing clone mutation
  (`clone-init-avatar`, `clone-sync-reupload`, …).
- No live Telegram call at any point while *writing* this plan or its
  tests. Track A's live acceptance and Track B's Task 4 measurement are
  owner-gated and happen against the real `[икона]` clone, after the
  relevant code merges (Task 4 is itself the exception that must run live
  — see its own gating note).

---

## Track A — carry the pinned message

### 1. Resolve the pin target: pure decision logic, no RPCs yet

This is the part of ADR-0055 decisions 1–4 that has no network shape and
should be fully covered by pure unit tests before any Telethon call is
written. Add a small module `src/tgcli/clone/pin.py` (new row in
`docs/MAP.md`'s clone/ table), mirroring the existing pure-decision style of
`clone/transport.py` and `clone/batching.py`.

- [x] Test (`tests/test_clone_pin.py`, new file): a pure function —
      suggested shape `decide(source_pinned_msg_id: int | None, id_map:
      dict[str, int], state: PinState) -> PinDecision` where `PinState`
      carries whatever CloneState already knows (see below) — returns
      `status="unmapped", destination_id=None` when
      `source_pinned_msg_id is None`.
- [x] Test: `source_pinned_msg_id` set but absent from `id_map` →
      `status="unmapped", destination_id=None`. Covers all three causes
      the ADR names (service message, skipped-unsupported post, deleted
      source) identically — the function only sees an id, not why it is
      missing.
- [x] Test: `source_pinned_msg_id` maps via `id_map`, and the clone has
      never pinned before and has not recorded the destination as occupied
      → `status="set", destination_id=<mapped id>` (the caller is
      responsible for actually sending the RPC; this function only decides
      intent).
- [x] Test: the clone has already pinned once before — `pinned_dest_id` is
      already recorded in state (see field design below) →
      `status="unchanged", destination_id=<the recorded id>`, **regardless**
      of what `source_pinned_msg_id` currently maps to. This is decision 3
      (never re-pin, never unpin) — assert it holds even when the source's
      current pin now maps to a *different* destination id than the one
      already recorded.
- [x] Test: the clone has previously recorded the destination as occupied
      (owner's own manual pin observed once, see Task 2) →
      `status="occupied", destination_id=None`, again regardless of what
      `source_pinned_msg_id` currently maps to, and without re-deriving it.
- [x] Implement `decide()` plus the two new `CloneState` fields it reads:
      `pinned_dest_id: int | None = None` (the destination id this clone
      itself pinned, once it succeeds) and `pin_occupied: bool = False`
      (the destination already had a foreign pin the one time this clone
      checked). Both are additive with safe defaults in `to_dict`/
      `from_dict` (`.get(key, default)`) — no `state.VERSION` bump, same
      pattern as when `avatar_photo_ids` was added. Add validation in
      `CloneState.from_dict`: `pinned_dest_id` when present must be a
      positive int; `pin_occupied` and a non-None `pinned_dest_id` are
      mutually exclusive (state that ends up in both is corrupt — raise
      `ValueError` in `from_dict`, matching the existing fail-closed style
      for `topic_map`/`discussion_id_map`).

### 2. Live resolution + the pin RPC itself

This is where the plan has to make a call the ADR's Consequences section
does not fully spell out, and you should read this note before writing
code — **flag any change of approach with the reviewer, don't silently
pick a different one.**

> **RPC-count note.** ADR-0055's Consequences section budgets "two extra
> RPCs per completing run (`GetFullChannelRequest` on the source, and the
> pin itself when needed)". That accounts for reading the source's current
> `pinned_msg_id` and for the `UpdatePinnedMessage` call, but decision 2
> ("if the destination already pins anything, the clone leaves it alone")
> requires knowing the destination's *current* pin state too, which
> `GetFullChannelRequest` on the source cannot tell you — the two peers are
> different channels. Reading the destination's `ChannelFull.pinned_msg_id`
> needs its own `GetFullChannelRequest(destination)` call.
>
> Resolve this with a design that keeps the *steady-state* cost matching
> the ADR's estimate, and only pays the extra RPC on the one run where it
> actually matters:
> - Once `CloneState.pinned_dest_id` or `pin_occupied` is set, **no RPCs at
>   all** run on later completing syncs — `decide()` alone answers
>   `unchanged`/`occupied` from state. This is the common case after the
>   first resolution and it is genuinely zero extra cost, which is where
>   most of the ADR's "not on every batch of a long run" framing is
>   earned.
> - On a run where neither field is set yet: always read the source's
>   current `pinned_msg_id` (1 RPC — the one the ADR names). If it does not
>   map yet, stop there (`unmapped`, 1 RPC total, nothing persisted, tried
>   again next completing run).
> - Only when it *does* map for the first time, read the destination's
>   current `pinned_msg_id` (the RPC the ADR's estimate omits) to decide
>   `set` vs `occupied`. This "3rd RPC" happens exactly once per clone,
>   ever — the run where the pin transitions out of `unmapped`.
>
> Write this reasoning as a code comment above the phase entry point (not
> just in a commit message) so a future reader does not "fix" the extra
> read back down to match the ADR's rough estimate.

- [x] Test (boundary, `tests/test_clone_pin.py` or
      `tests/test_cli_clone_sync.py`): a completing sync run whose source
      currently pins a mapped post, first time through (state has neither
      field set), sends **exactly**
      `functions.channels.GetFullChannelRequest` for the source,
      `functions.channels.GetFullChannelRequest` for the destination
      (destination's `ChannelFull.pinned_msg_id is None`), then **exactly
      one** `functions.messages.UpdatePinnedMessageRequest` with
      `peer=<destination InputPeer>`, `id=<mapped destination message id>`,
      `silent=True`, and no `unpin` set. Assert on the recorded request
      sequence and exact field values, not just call count.
- [x] Test: destination's `ChannelFull.pinned_msg_id` is already non-null
      (an owner's manual pin, unrelated to any mapped source post) →
      no `UpdatePinnedMessageRequest` is sent, `clone_state.pin_occupied`
      becomes `True`, `pinned_dest_id` stays `None`, JSON reports
      `status: "occupied"`.
- [x] Test: second completing run after a first-run `"set"` — assert
      **zero** `GetFullChannelRequest` calls of any kind fire (state alone
      answers `"unchanged"`), even though the fake client's source now
      reports a *different* `pinned_msg_id` that maps to a different post.
- [x] Test: source has no linked pin at all (`pinned_msg_id is None`) →
      one `GetFullChannelRequest(source)` call, no destination read, no
      pin RPC, `status: "unmapped"`, state untouched (tried again next
      completing run).
- [x] Test: `mutate`'s existing FloodWait handling covers the pin RPC too —
      a `FloodWaitError` on `UpdatePinnedMessageRequest` persists the clone
      cooldown and the run exits 5 without marking `pinned_dest_id`
      (reuse the existing `_with_cooldown`/`mutate` seam rather than a
      bespoke retry).
- [x] Test: `safety.append_audit("clone-sync-pin", account_alias, {...})`
      (or whatever tag name the reviewer prefers — keep it consistent with
      `clone-sync-reupload`'s shape: `clone_id`, source id, destination id)
      fires before the `UpdatePinnedMessageRequest` dispatch, and does
      **not** fire on `unmapped`/`occupied`/`unchanged` outcomes (nothing
      was mutated).
- [x] Implement in `clone/pin.py`: an async entry point (mirroring
      `comments.sync_phase`'s shape) — something like
      `async def sync_phase(tg, clone_state, source_entity, destination,
      mutate, cooldown, account_alias) -> dict` returning the `pinned`
      JSON sub-object. It needs the same three-way `User`/`Chat`/`Channel`
      dispatch `_copy_profile` already has to read the source's full chat
      generically (a shared helper is fine if it falls out naturally;
      don't force it). The destination side is always
      `channels.GetFullChannelRequest` since destinations are always
      Channel-shaped (broadcast or forum — but see the forum gate below).

### 3. Wire into `sync_text` and CONTRACT

- [x] Test (`tests/test_cli_clone_sync.py`): a completing run
      (`more is False` after both the posts and, when enabled, comments
      legs) with a mapped pinned post calls the pin phase and the JSON
      response carries
      `"pinned": {"source_id": 12, "destination_id": 9, "status": "set"}`.
- [x] Test: a run that stops early (`more is True`, limit hit inside either
      leg) does **not** call the pin phase at all — assert zero
      `GetFullChannelRequest`/`UpdatePinnedMessageRequest` calls attempt,
      and the JSON still carries a `pinned` key (consistency with how
      `comments` is always present) reflecting only what state already
      knows: `{"source_id": null, "destination_id": null,
      "status": "unmapped"}` when nothing has resolved yet, or the
      previously-resolved `set`/`occupied` values when it has — never a
      value implying a live check happened this run.
- [x] Test: a forum-destination clone (`clone_state.destination_kind ==
      "forum"`) never gets a `pinned` key in its sync JSON, completing run
      or not, and the pin phase is never invoked (verify no
      `GetFullChannelRequest`/`UpdatePinnedMessageRequest` calls beyond
      whatever the forum topic-handling paths already make).
- [x] Wire the call into `sync_text` in `src/tgcli/commands/clone.py`,
      right after the existing comments-phase block (around line
      1062–1074, the `if clone_state.comments == "enabled" and not more:`
      block) and before `progress.phase("roster")` (line 1075). Add
      `"pinned": pinned_result` into the `data["sync"]` dict built around
      lines 1089–1099.
- [x] CONTRACT §11: add the `pinned` object to the `sync` JSON shape
      (§11's example near line 1235), describing: when it appears (never
      for forum destinations), its four `status` values (`set`,
      `unchanged`, `unmapped`, `occupied`) and what triggers each, that
      `silent=True` is always used, that unpinning is never mirrored, and
      — honestly, not copying the ADR's rounded estimate — that the RPC
      cost is one `GetFullChannelRequest` (source) on every completing run
      until resolved, a second `GetFullChannelRequest` (destination) only
      on the run the pin first becomes mappable, zero on every run after
      resolution, plus one `UpdatePinnedMessageRequest` when a pin is
      actually placed.
- [x] `docs/MAP.md`: add the `clone/pin.py` row to the clone/ table
      (`pure pin-decision + live pin-carry phase (ADR-0055)`).

---

## Track B — photo downscaling

### 4. Measure before touching any code (owner-gated, live)

> **2026-07-25 implementation note:** Task 4 was not run in this session —
> no live Telegram session / owner gate. Recorded as remaining owner-gated;
> Track B Task 6 proceeds unconditionally per ADR-0055 decision 6 and the
> mission brief. Task 5's CONTRACT re-encode sentence is deferred until
> Task 4 produces evidence.


**This is the first photo task and it changes no code.** Its only output is
a DEVLOG entry and a go/no-go call for Task 6. Do not write or propose a
fix before this runs. This step needs the real Telegram network and the
owner's session — it cannot be done from this planning session, and it
should not be simulated with fixtures, since the whole point is to observe
what Telegram actually does.

- [ ] Pick one of the five audited photos (source message ids 15, 33, 35,
      58, 81 against the `[икона]` clone) — message 15 is the one the ADR
      already has numbers for (source 1024×1024 / 45 216 bytes, clone
      800×800 / 41 902 bytes).
- [ ] Using `tg` against the source, read the message's `photo.sizes` and
      identify the largest `PhotoSize` by `w`/`h` (or byte count) —
      confirm it really is 1024×1024 / 45 216 bytes and not something the
      audit already got from a stale source.
- [ ] Download that exact largest size to disk (a plain `tg media
      download`, not the striped path — this photo is 45 KB, far under
      `CHUNK_SIZE`, so it never touches `download_striped` and Task 6's
      fix is provably irrelevant to it) and record the byte count on disk.
      This isolates "did tgcli fetch the wrong size" from "did Telegram
      shrink what we sent it": if the on-disk bytes already come back
      short of 45 216, the defect is upstream of upload, in the download
      step — go find it in `_download_for_reupload`/`download_media`, not
      in Task 6.
- [ ] If the downloaded bytes match the source's largest size (45 216 for
      message 15), upload those exact same bytes as
      `types.InputMediaUploadedPhoto` to a disposable/scratch chat the
      owner controls (not necessarily re-running the whole clone), then
      immediately read back what Telegram reports for that sent message's
      `photo.sizes` — largest width/height and byte count.
- [ ] Record all three numbers (source largest, on-disk after download,
      server-reported after upload) in the DEVLOG entry for this session,
      plus the explicit conclusion:
      - If the on-disk bytes are already short → tgcli's download path has
        a real defect distinct from Task 6; open a fresh reproducing test
        against whatever path is at fault before writing any fix (new task,
        not yet in this plan — do not fold it into Task 6, which is scoped
        to the striped-download size-selection bug only).
      - If on-disk bytes match the source but the server-reported
        post-upload size is smaller → this is Telegram re-encoding the
        upload. Proceed to Task 5 (CONTRACT documentation only, no code
        change, per ADR-0055 decision 7 — do not build a workaround).
      - Either way, write explicitly in the DEVLOG (and later, the
        CHANGELOG per Task 6) that this finding is **not** about the
        striped-download `sizes[-1]` defect — all five audited photos are
        under 512 KB and never reach `download_striped` — so Task 6 must
        not be presented as fixing them.

### 5. Document the finding (conditional on Task 4's outcome)

- [ ] If Task 4 found Telegram re-encoding: add one CONTRACT §11 sentence
      near the reupload description (around the "Downloaded files live
      only in a temporary directory…" paragraph, line ~1216–1225 today)
      stating plainly that a reuploaded photo may come back smaller than
      the source because Telegram re-encodes `InputMediaUploadedPhoto`
      server-side, and that tgcli does not work around this (documents are
      not substituted for photos — ADR-0055 decision 7). This is a
      CONTRACT change, so it ships as its own tagged patch release
      (Task 7), separate from Track A and from Task 6 if they are not
      ready together.
- [ ] If Task 4 instead found a genuine tgcli-side download defect: do not
      write this CONTRACT sentence. Instead stop here, write up the defect
      with a reproducing scenario in the DEVLOG, and treat it as new scope
      requiring its own ADR/plan increment (out of this plan — maintenance
      mode requires an explicit owner request or bug ticket before writing
      that fix).

### 6. Fix striped download's unordered `sizes[-1]` trust (unconditional)

Independent of Task 4/5's outcome — ADR-0055 decision 6 is explicit that
this is worth fixing on its own terms, and equally explicit that it is
**not** the explanation for the audited five photos (all under
`CHUNK_SIZE` = 512 KB, so they never reach `download_striped` at all).

Context: `transfer.download_striped` (`src/tgcli/transfer.py:34`) passes
`media` straight into `tg.iter_download(media, ...)`. Telethon's own
`utils._get_file_info` (confirmed by reading the installed Telethon
source) builds the download location for a `types.Photo` from
`location.sizes[-1]` — trusting list order, not size. Meanwhile
`media_byte_size()` (`transfer.py:169`, used by both call sites —
`commands/clone.py:629` `_download_for_reupload` and
`commands/media.py:284` `_download_parallel` — to size the preallocated
file and the chunk math) reads `message.file.size`, and Telethon's own
`File.size` property (confirmed in the installed source) already computes
the **max** byte count across `photo.sizes` via `utils._photo_size_byte_count`,
not the last element. So today, whenever a photo's `sizes` list is not
sorted ascending, `download_striped` preallocates the file to the *correct*
largest size but then streams from whatever `sizes[-1]` happens to be —
silently writing a truncated/wrong-content file with no error, because
`iter_download`'s own internal size bookkeeping (unless told otherwise)
follows `sizes[-1]` too, not the `size=` parameter we pass in.

- [x] Test (`tests/test_transfer.py` or new
      `tests/test_clone_transfer_photo_size.py`): construct a fake
      `types.MessageMediaPhoto` wrapping a `types.Photo` whose `sizes` list
      has a genuinely smaller size last (e.g., `[large_1024, small_256]`
      order — the adversarial case the current code silently mishandles).
      Call `download_striped` with a fake `tg.iter_download` that records
      what it was asked to fetch. Assert the resulting request/location
      corresponds to the **largest** size (1024, by dimensions or by
      `_photo_size_byte_count`), not `sizes[-1]` (256).
- [x] Test: the already-correct case — `sizes` already ascending, largest
      last — produces byte-identical behavior to today (regression guard;
      this must not change output for the common case Telegram usually
      sends).
- [x] Test: a `types.MessageMediaDocument` (non-photo) path is completely
      unaffected — `_get_file_info`'s document branch uses `location.size`
      directly, there is only one size, nothing to reorder.
- [x] Implement inside `download_striped` itself (not at either call
      site), so both `tg media download --parallel` and clone's reupload
      path get the fix uniformly (AGENTS.md mirror-fix rule — this is
      exactly the kind of shared-code bug the rule exists for). The
      simplest correct approach: when the unwrapped media is a
      `types.Photo` with more than one size, build a shallow copy with
      `sizes` reordered so the true largest (by `_photo_size_byte_count`,
      matching what `File.size` already uses, so the two stay consistent)
      is last, and pass that copy into `iter_download` instead of the
      original — this keeps `photo.dc_id` intact and avoids having to
      hand-construct an `InputPhotoFileLocation` (which would lose the
      photo's `dc_id` unless separately threaded through `iter_download`'s
      `dc_id=` kwarg; reordering `sizes` sidesteps that risk entirely).
- [x] CONTRACT.md: no behavior visible at the CLI boundary changes for the
      common (already-sorted) case, so this may not need a CONTRACT
      sentence — confirm with the reviewer; if the reviewer wants one, add
      a short note under §11's reupload paragraph that striped downloads
      select the largest photo size explicitly rather than trusting
      Telegram's size ordering.
- [x] CHANGELOG entry for this task must say, explicitly, that this fix
      does **not** explain or resolve the `[икона]` audit's five smaller
      photos (source messages 15/33/35/58/81) — they are all under the
      512 KB striped threshold and were never touched by this code path —
      and must not be phrased in a way a reader could mistake for "the
      photo bug is fixed."

### 7. Release (Track B)

- [ ] CHANGELOG + double version bump (both `pyproject.toml` and
      `src/tgcli/__init__.py`); DEVLOG entry for the session; PR →
      `reviewer` → CI → merge → tag. If Task 5 and Task 6 are ready
      together, one release covers both; if Task 5 is still pending Task
      4's live measurement, Task 6 may ship alone first.

---

## Release (Track A)

- [x] CHANGELOG + double version bump; `docs/MAP.md` row for `clone/pin.py`
      (done in Task 3); DEVLOG entry; PR → `reviewer` → CI → merge → tag.

## Live acceptance (Track A only, owner-gated, after merge)

- [ ] Run `clone sync` against the real `[икона]` clone. Source pins post
      12 (destination post 9 via `id_map`); the destination's
      `pinned_msg_id` is currently `None`. Confirm the run's JSON reports
      `"pinned": {"source_id": 12, "destination_id": 9, "status": "set"}`,
      that the pin arrives silently (no notification), and that a second
      immediate `clone sync` reports `"status": "unchanged"` with no new
      `GetFullChannelRequest` calls. Stop on exit 5; never retry in a loop
      (ADR-0045).

Track B has no separate live acceptance step: Task 4 *is* the live,
owner-gated step, and it must run before Task 5's documentation task can be
written honestly.
