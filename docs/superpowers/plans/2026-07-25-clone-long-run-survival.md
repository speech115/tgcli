# Surviving a long clone run — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. Scope is fixed by
> [ADR-0052](../../decisions/ADR-0052-clone-flood-short-wait-and-media-cache.md)
> (title: "Surviving a long clone run — short flood waits and media reuse"),
> which amends [ADR-0045](../../decisions/ADR-0045-clone-flood-containment.md)
> in one clause: what `clone sync` does with a *short* `FloodWaitError`.
> **The ADR file does not exist in the tree yet** — task 1's commit adds
> `docs/decisions/ADR-0052-clone-flood-short-wait-and-media-cache.md` (content
> below, copied verbatim) plus its row in `docs/decisions/README.md`, because
> every later task cites it. One PR, one tagged patch release (ADR-0038).

**Goal:** A `clone sync` that meets a short (`≤ 60s`) `FloodWaitError` pauses
in the foreground and continues the same invocation instead of exiting 5 and
throwing away a warm process. A `FloodWaitError` mid-reupload no longer
discards media already downloaded for the batch.

**Non-goals:** a flag or config key for `SHORT_WAIT`/`WAIT_BUDGET` (ADR-0052
decision 3 — both are constants); more than one retry per call; changing
ADR-0045's account-scoped cooldown, its lazy next-invocation enforcement, or
the "never retry in a loop" exit-5 posture once a wait is too long or the
budget is spent; per-request wait-budget accounting more precise than a
cumulative counter; anything about ADR-0051's window interleaving (orthogonal,
either may land first or after this).

## Global constraints

- TDD; no network in tests; **tests must not actually sleep** — the sleep
  goes behind an injectable seam and the suite monkeypatches it (this repo
  already has the pattern: `tests/test_session.py`'s
  `monkeypatch.setattr("telethon.client.users.asyncio.sleep", fake_sleep)`
  which appends to a list and returns immediately). Assert *that* a wait was
  requested and for how many seconds, never by waiting for it.
- Boundary tests assert exact Telethon request types/arguments for any call
  site whose request-construction shape changes (AGENTS.md).
- CONTRACT.md updates land in the same commit as the code that changes the
  behavior it describes.
- `scripts/gate.sh` before every commit.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- No live Telegram call at any point in this plan. Live acceptance is
  owner-gated and happens after merge.

## Ordering note

Tasks 1 → 2 → 3 are strictly sequential: task 2's retry-once logic is built
on task 1's thunk seam, and task 3's budget gate hooks into task 2's retry
branch. Landing 2 or 3 without 1 has nothing to attach to. Task 4 (media
cache) is independent of 1–3 and may be done in parallel, but task 5 (`store
cleanup`) depends on task 4's on-disk layout and must follow it.

### 1. Convert the cooldown seam from a coroutine to a re-runnable thunk

`_with_cooldown` (`src/tgcli/commands/clone.py:269-277`) is called today as
`await _with_cooldown(tg(request), clone_state)` — the caller builds the
coroutine once and hands it over. A Python coroutine object is single-use: it
cannot be re-awaited after it has already run to an exception. Retrying "the
same awaitable" (ADR-0052 decision 1) therefore requires callers to pass a
**zero-argument callable that builds a fresh awaitable** (`make_awaitable`),
not the awaitable itself. This task does that conversion only — behavior
stays byte-identical (catch `FloodWaitError`, arm both cooldowns, re-raise) —
so it can be reviewed and merged as a pure, low-risk refactor before task 2
adds new behavior on top.

Every direct or indirect caller of `_with_cooldown` needs its argument
changed from `tg(request)` (or `download_striped(...)`, `tg.download_media(...)`,
`tg.upload_file(...)`, `tg.download_profile_photo(...)`) to
`lambda: tg(request)` etc. Grep `_with_cooldown(` and `invoke(` under
`src/tgcli/` to enumerate every site before starting; the known ones from
reading the code are:

- `src/tgcli/commands/clone.py`: `_with_cooldown` itself; `_mutate` (~280-284);
  `_copy_profile`'s local `cooldown` closure and its ~4 call sites (~287-355);
  `_uploaded_media`'s `invoke` closure (~608-618, passed as `upload_parts(...,
  invoke=invoke)`); `_download_for_reupload`'s two branches (~629-655, the
  striped-download call and the plain `tg.download_media` call);
  `_forward_batch`'s local `cooldown` closure (~782-783) and the two places it
  is threaded further as `invoke=cooldown` into `reforward.locate` (~790) and
  as `invoke=lambda awaitable: _with_cooldown(awaitable, clone_state)` into
  `snapshot.render` (~836) — this second one is already lambda-shaped but
  wraps a pre-built `awaitable`, not a thunk; it needs the same fix one layer
  down.
- `src/tgcli/transfer.py`: `upload_parts`'s `Invoke` type (`~line 21`) and its
  internal `run()` helper (`~120-123`), which today does
  `result = await run(tg(request))` inside the per-part loop
  (`~142`). `upload_parts` has no other caller in the codebase (only
  `commands/clone.py:_uploaded_media` imports it — confirmed by grep), so its
  `invoke` contract is safe to change outright. `download_striped` needs **no**
  change: `commands/clone.py` already wraps the *entire* call in one
  `_with_cooldown`, and `download_striped`'s own failure path already
  `unlink`s any partial destination file before re-raising, so retrying the
  whole call from a fresh thunk is safe as-is.
- `src/tgcli/clone/attribution.py`: `author_of` and `forwarded_author_of`
  accept a `cooldown` callback — find their internal `cooldown(tg(...))` call
  sites and wrap the argument in a thunk.
- `src/tgcli/clone/reforward.py`: `locate`'s `invoke(awaitable)` call
  (~line 171) and whatever it forwards to (`source_group(..., invoke=invoke)`,
  ~line 57).
- `src/tgcli/clone/snapshot.py`: `render`'s two `invoke(tg(cast))` /
  `invoke(tg(retract))` call sites (`_capture_breakdown`, ~120 and ~132).
- `src/tgcli/clone/progress.py`: `approximate_total`'s
  `invoke(tg.get_messages(entity, limit=0))` call and its `Invoke` type
  annotation/docstring.

- [ ] Test (`tests/test_transfer.py`): update
      `test_upload_parts_uses_invoke_wrapper` (~line 220) for the new
      thunk-based `Invoke` contract; add a case where the fake `invoke`
      is called once per part with a *callable*, not a coroutine, and
      calling it twice produces two independent, non-exhausted awaitables
      (proves the thunk is genuinely re-runnable, not just re-typed).
- [ ] Test (`tests/test_clone_flood.py` or a new
      `tests/test_clone_cooldown.py`): a fake `tg` whose call returns a
      coroutine that raises `FloodWaitError` once then succeeds — driven
      through `_with_cooldown` with the *old* single-attempt behavior
      (task 1 does not add retry yet) still raises on first failure,
      proving the refactor introduced no accidental retry.
- [ ] Test: every boundary test in `tests/test_cli_clone_sync.py` that
      already asserts exact Telethon request types for reuploads, forwards,
      attribution, reforward, and poll capture still passes unchanged —
      run the full file, not a subset, since this task's only allowed
      effect is internal call shape.
- [ ] Implement the thunk conversion across the files listed above.
      `_with_cooldown(make_awaitable, clone_state)` becomes
      `awaitable = make_awaitable(); try: return await awaitable except
      FloodWaitError: ... raise` — same body, new argument shape.

### 2. Retry a short flood wait once, in the foreground, visibly

- [ ] Test: `FloodWaitError(seconds=3)` on the first attempt →
      `_with_cooldown` calls the injected sleep exactly once with `3 + 1 = 4`
      (the one-second margin from ADR-0052 decision 1), then calls
      `make_awaitable()` again and returns its result. Use the
      `monkeypatch.setattr("tgcli.commands.clone.asyncio.sleep", fake_sleep)`
      pattern from `tests/test_session.py`; `fake_sleep` appends to a list and
      returns immediately, so the test runs in milliseconds.
- [ ] Test: the retried call also raises `FloodWaitError` (second failure) →
      propagates immediately without a second sleep; the cooldown is armed at
      the *second* deadline (both `clone_state.set_cooldown`/`state.save` and
      `flood.arm_cooldown` reflect it), matching "raised as it is today" for
      the second failure.
- [ ] Test: `FloodWaitError(seconds=61)` (over `SHORT_WAIT = 60`) → raises
      immediately, sleep is never called — today's behavior, unchanged.
- [ ] Test: the cooldown is armed **before** the sleep is invoked (ADR-0052
      decision 4) — make `fake_sleep` itself assert (or record and let the
      test assert after) that `flood.cooldown_deadline(account_user_id)` is
      already set at the moment sleep is called, so a process "killed"
      mid-sleep would already have left the durable cooldown.
- [ ] Test: during the wait, a line naming the seconds remaining is written
      to stderr (ADR-0052 decision 5) via `capsys`; `--json` stdout is
      unchanged (still exactly one JSON document, no extra keys).
- [ ] Implement in `_with_cooldown` (`src/tgcli/commands/clone.py`): add
      `import asyncio`; on catching `FloodWaitError`, arm both cooldowns
      exactly as today, then if `exc.seconds <= flood.SHORT_WAIT`: print the
      stderr line (reuse `tgcli.output.note` or add a small helper next to
      `clone/progress.py`'s existing stderr conventions — non-contractual,
      ADR-0049 style), `await asyncio.sleep(exc.seconds + 1)`, call
      `make_awaitable()` again and `return await` it, letting a second
      `FloodWaitError` propagate through the same `except` handling
      unconditionally (no further retry). `SHORT_WAIT` lives in
      `src/tgcli/clone/flood.py` as a module constant (see task 3).
- [ ] CONTRACT.md §11: replace "Upload/send FloodWait persists the clone
      cooldown" (~line 1222) and "FloodWait persists the clone cooldown and
      exits 5 without advancing the current message" (~line 1231) with
      wording that a `FloodWaitError` of at most `SHORT_WAIT` seconds is
      waited out once in the foreground (stderr progress line, non-
      contractual) and the same request retried; a second failure, or a wait
      over `SHORT_WAIT`, behaves exactly as documented today (cooldown
      persists, exit 5, current message not advanced).

### 3. Bound total waiting per process with a wait budget

- [ ] Test (`tests/test_clone_flood.py`): a fresh budget allows repeated
      short waits (e.g. three separate 3-second `FloodWaitError`s across three
      different `_with_cooldown` calls in the same simulated `sync_text` run)
      to all retry, since their sum stays under `WAIT_BUDGET = 180`.
- [ ] Test: once the cumulative seconds already spent waiting reaches or
      exceeds `WAIT_BUDGET`, the next `FloodWaitError` — even a 1-second one —
      raises immediately without calling sleep. Assert the exact boundary:
      spend exactly 180s across prior waits, then confirm the 181st second
      of demand is refused.
- [ ] Test: the budget is scoped to one `sync_text` invocation, not
      persisted — two separate calls into `sync_text` (simulating two
      separate `tg clone sync` processes) each start with a full 180-second
      budget; nothing under `~/.local/state/tgcli/` records spent seconds.
- [ ] Implement `SHORT_WAIT = 60` and `WAIT_BUDGET = 180` as module constants
      in `src/tgcli/clone/flood.py`, plus a small in-memory (never persisted,
      never touches `flood.save`/`atomic.replace_text`) budget holder — e.g.
      a tiny class with a `spent: float` counter and a
      `try_spend(seconds) -> bool` method that returns `False` without
      mutating state once `spent >= WAIT_BUDGET`, otherwise increments
      `spent` and returns `True`. `sync_text` (`src/tgcli/commands/clone.py`,
      ~line 900) constructs exactly one instance at the top of the function
      and threads it through every call site touched in tasks 1–2 alongside
      `clone_state` (same closures, same parameter list — it travels with
      `clone_state` everywhere `clone_state` already travels). `_with_cooldown`
      only attempts the sleep+retry from task 2 when
      `exc.seconds <= SHORT_WAIT and budget.try_spend(exc.seconds + 1)`;
      otherwise it raises immediately as it does today for a long wait.
- [ ] CONTRACT.md §11: note the 180-second per-process bound next to the
      short-wait retry text added in task 2, so the exit-5 fallback is
      documented as bounded rather than unconditional.

### 4. Persistent, reusable media cache for reupload batches

`_reupload_batch` (`src/tgcli/commands/clone.py:662-745`) downloads into
`tempfile.TemporaryDirectory(prefix="tgcli-clone-reupload-")` (~677), and
`_download_for_reupload` (~629-655) writes each message's media to
`workdir / f"src-{message.id}"`. Because the directory is temporary, a
`FloodWaitError` anywhere in the upload phase discards every byte already
downloaded for the batch — the whole reason for this task.

- [ ] Test (`tests/test_cli_clone_sync.py` or a new
      `tests/test_clone_media_cache.py`): `_reupload_batch` downloads into
      `state.clones_dir() / f"{clone_state.clone_id}-media"` instead of a
      `tempfile.TemporaryDirectory` — assert the path directly.
- [ ] Test: pre-seed `<cache>/src-<id>` with a file whose byte size matches
      exactly what `media_byte_size(message)` reports for that source
      message → `_download_for_reupload` returns that path **without**
      issuing any download RPC (assert on the fake `tg`'s call log/count).
- [ ] Test: pre-seed the same filename with a **different** size → the stale
      file is re-downloaded (overwritten), proving the reuse rule is
      name **and** size, not name alone.
- [ ] Test: a batch that sends successfully leaves the cache directory
      removed afterward (empty, or the directory itself gone — pick one and
      assert it).
- [ ] Test: a batch that raises partway through the send (simulate
      `FloodWaitError` inside `_uploaded_media`/`_mutate` after at least one
      message has been downloaded) leaves the downloaded file(s) **on disk**
      after the exception propagates out of `_reupload_batch` — this is the
      behavior the whole task exists to prove.
- [ ] Test: `media_byte_size(message)` returning `None` (a media kind whose
      size can't be predicted) always re-downloads — no reuse claim without a
      trustworthy size to check against.
- [ ] Implement: replace the `with tempfile.TemporaryDirectory(...)` block in
      `_reupload_batch` with an explicit persistent directory
      (`mkdir(parents=True, exist_ok=True)`); add the reuse check at the top
      of `_download_for_reupload` (before either the striped or plain-download
      branch, since both already compute `size = media_byte_size(message)`);
      delete the directory tree (`shutil.rmtree`, ignore-errors) only after
      the batch's send call returns successfully — not in a `finally`, since
      the whole point is that a raised exception must leave it intact.
- [ ] CONTRACT.md §11: replace "Downloaded files live only in a temporary
      directory and are removed on success or failure" (~line 1219-1220) with
      the persistent-cache path, the name+size reuse rule, and "removed after
      a successful send; left on disk after a failed one, so a retry does not
      re-download."

### 5. `store stats` / `store cleanup` learn the media cache

- [ ] Test (`tests/test_commands_store.py`): `store_cmd.scan(root)` reports a
      new bucket for `clones/*-media` directories — count of directories and
      total bytes — distinct from the existing aggregate `"clones": {"bytes":
      ...}` figure (which already includes them via `_dir_bytes`, so this is
      additive detail, not a behavior change to the existing key).
- [ ] Test: `store cleanup --confirm` removes a `clones/<id>-media` directory
      and everything under it, while leaving the sibling `clones/<id>.json`
      clone state file untouched — assert both outcomes in one test so a
      future change can't quietly start deleting live clone state.
- [ ] Test: dry-run (`--confirm` omitted) reports the directory in
      `would_remove` and deletes nothing, consistent with every other bucket.
- [ ] Test: `--older-than` gates the media cache the same way it gates other
      buckets, measured from the directory's mtime (there is no `expires_at`
      record for a cache directory, so this always takes the mtime-fallback
      path already used elsewhere in `store.py`).
- [ ] Implement in `src/tgcli/commands/store.py`: extend `scan()` with a
      `clone_media_cache` (or similar) bucket; extend `_deletable_paths()` to
      glob `root / "clones"` for `*-media` directories and select them under
      the same `older_than` rule as other buckets; extend `stats_rows()` and
      `cleanup_rows()` to surface it in `--plain` output.
- [ ] CONTRACT.md §5.05: document the new `store stats` bucket and that
      `store cleanup --confirm` removes abandoned clone media caches (never
      the clone's own `.json` state), matching ADR-0052 decision 7 ("the
      cache is owned state, not litter").

### 6. Release

- [ ] `docs/MAP.md`: `flood.py`'s one-liner gains "+ per-run wait budget
      (ADR-0052)"; note the media cache path if `state.py` or `clone.py`
      gained a named helper for it.
- [ ] CHANGELOG.md: new section naming ADR-0052, under both the short-wait
      retry/budget behavior and the media cache/`store cleanup` behavior.
- [ ] Double version bump (`pyproject.toml` and `src/tgcli/__init__.py`):
      `1.2.9` → `1.2.10` (patch; this is a feature/fix, not an owner-declared
      milestone).
- [ ] `docs/DEVLOG.md`: one entry for the session.
- [ ] PR → `reviewer` subagent → green CI (PR-event run) → merge → tag
      `v1.2.10` per `docs/agents/release.md`.

### 7. Live acceptance (owner-gated, after merge)

- [ ] Resume an in-progress clone known to hit short `FloodWaitError`s
      (e.g. `[икона]`, per ADR-0052's motivating run) and confirm a single
      `clone sync` invocation now survives multiple 3–8 second waits without
      exiting, with visible stderr progress lines during each pause.
- [ ] Confirm a reupload batch that hits a `FloodWaitError` mid-upload (or is
      interrupted with Ctrl-C between download and send) leaves the partially
      downloaded file under `~/.local/state/tgcli/clones/<clone_id>-media/`,
      and that re-running `clone sync` does not re-download it.
- [ ] Confirm `tg store stats` reports the cache and `tg store cleanup
      --confirm` removes it once the clone is done or abandoned.
- [ ] Stop on exit 5 (long wait or spent budget); never retry in a loop
      (ADR-0045).
