# The `--json` error envelope on stdout — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. Scope is fixed by
> [ADR-0053](../../decisions/ADR-0053-json-error-envelope-on-stdout.md),
> already `accepted` and merged into `docs/decisions/`. Task 3 is **not**
> ADR-0053 work — it is a one-line correction to ADR-0049's stderr progress
> line (non-contractual format, no ADR needed) that rides in this same PR
> because it touches the same failure-visibility surface the owner was
> staring at while writing ADR-0053. One PR, one tagged patch release
> (ADR-0038).

**Goal:** A failing `--json` invocation writes the error envelope to stdout
(the run's single JSON document, per CONTRACT §2) in addition to the
existing stderr mirror, which stays byte-for-byte unchanged. Human,
`--plain`, `tg batch`, and every exit code are untouched. Corollary: the
`clone sync` comments-phase progress line stops permanently showing `~?`
for the denominator.

**Non-goals:** touching `tg batch`'s per-op `{"ok": false, ...}` lines
(ADR-0032, out of scope per ADR-0053 §4); changing `PartialFailure`'s
stdout path in `cli.py` (it already writes `err.data` to stdout on
`--json` and was never the bug — leave it alone); any exit-code
renumbering; a window-size or format flag for progress; per-post
resolution of the comments-phase total (one `get_messages(limit=0)` per
phase is the existing ADR-0049 shape, not per batch).

## Global constraints

- TDD; no network in tests.
- CONTRACT.md updates land in the same commit as the code (§2 only —
  task 3 is explicitly non-contractual and touches no CONTRACT section).
- `scripts/gate.sh` before every commit.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- `output.py` remains the only module that writes to `sys.stdout`
  (module docstring, AGENTS.md "stdout is sacred"). The fix belongs
  entirely inside `emit_error`; do not add a second stdout write site in
  `cli.py`.
- No live Telegram call at any point in this plan.

### 1. `emit_error` writes the same line to stdout and stderr under `--json`

- [ ] Test (`tests/test_output.py`): extend
      `test_emit_error_json_mode_is_machine_readable` so it asserts on
      **both** `capsys.readouterr().out` and `.err` — same parsed JSON
      object on each stream, and the two raw strings are identical
      (byte-for-byte, not just JSON-equal — this is what rules out a
      future refactor that reformats one copy). Leave
      `test_emit_error_human_mode` untouched: it already proves stdout
      stays empty in human mode (no `capsys.readouterr().out` assertion
      is needed there beyond what it already implicitly covers via the
      shared `capsys` fixture — add an explicit `assert ... .out == ""`
      if it is not already implied by the existing assertion shape).
- [ ] Test (`tests/test_contract_exit_codes.py`): extend the three
      existing `--json` scenarios so each also parses stdout and asserts
      it equals the stderr envelope:
      - `test_readonly_mutation_is_exit_2` (`PolicyError` / `BLOCKED`)
      - `test_unknown_account_resolution_is_exit_3` (`ConfigError` /
        `CONFIG`)
      - `test_unknown_alias_lookup_is_exit_4` (`NotFoundError` /
        `NOT_FOUND`)
      Factor the duplicated parse into one local helper next to the
      existing `_error_code(capsys)`, e.g. `_stdout_error(capsys)`, and
      assert `_stdout_error(capsys) == _error_code(capsys)` (call each
      exactly once per test — `capsys.readouterr()` drains the buffers).
- [ ] Test (exit 5, `tests/test_cli_read.py::test_floodwait_maps_to_exit_5`):
      extend to also parse `capsys.readouterr().out` as JSON and assert
      its `error` object equals the one already asserted from `.err`
      (`code == "FLOOD_WAIT"`, `retry_after == 42`).
- [ ] Test (exit 1, `tests/test_cli_export.py`): the codebase's only
      concrete `RUNTIME`/exit-1 exception is `ExportError`
      (`src/tgcli/errors.py`) — nothing raises the bare `TgcliError`
      directly. Add `--json` to
      `test_export_messages_preserves_existing_destination_when_iteration_fails`
      (or add a sibling test using the same `OSError`-during-iteration
      fixture) and assert exit code 1, `error.code == "RUNTIME"` on
      *both* streams, and that the existing destination-preservation
      assertion (`destination.read_text() == "previous\n"`) still holds
      — the stdout write must not touch the export file.
- [ ] Implement: in `src/tgcli/output.py::emit_error`, when `as_json` is
      true, build the JSON line once and write it to both `sys.stdout`
      and `sys.stderr` — do not compute the payload twice, so the two
      writes are identical by construction rather than by coincidence:

      ```python
      def emit_error(err: TgcliError, *, as_json: bool) -> None:
          if as_json:
              payload = {"error": {"code": err.code, "message": str(err), **err.details}}
              line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
              sys.stdout.write(line)
              sys.stderr.write(line)
          else:
              note(f"error: {err}")
      ```

      No change needed in `cli.py`: `main()`'s `except TgcliError` branch
      and `PartialFailure`'s human-mode `else` branch both already call
      `output.emit_error(err, as_json=args.json)` and get the new
      behavior for free. `PartialFailure`'s `--json` branch does not call
      `emit_error` at all (it emits `err.data` directly) and is correctly
      out of scope — verify no test for it regresses.
- [ ] Run the full suite once, not just the touched files — `emit_error`
      is called from every command's error path; confirm nothing else
      asserted `capsys.readouterr().out == ""` on a `--json` failure path
      that now legitimately has content (grep test files for
      `out == ""` combined with `--json` and a nonzero exit before
      declaring this task done).

### 2. CONTRACT §2: state explicitly that the envelope lives on stdout first

- [ ] Update `docs/CONTRACT.md` §2. Current text says only that the
      error is "also mirrored to stderr", which is where the drift in
      ADR-0053's Context section came from. Add one sentence making the
      order explicit, in place, right after the existing mirror
      sentence: with `--json`, the error envelope is written to stdout
      as the run's single JSON document, then the identical line is
      copied to stderr as the existing last-line mirror — a `--json`
      caller may read either stream for the same object. Do not touch
      the `clone sync` progress-line paragraph in this task; that is
      task 3, and CONTRACT already correctly marks those lines
      non-contractual.

### 3. Fix: comments-phase progress denominator stuck at `~?` (ADR-0049 clarification, not ADR-0053)

Root cause: `SyncProgress.phase()` (`src/tgcli/clone/progress.py:95-98`)
resets `self._total = None` but never resets `self._total_resolved`,
which stays `True` once the posts leg has resolved it once.
`resolve_total()` (line 73) short-circuits on that flag, so every phase
after the first — `comments`, then `roster` — never re-queries and the
line prints `~?` for the rest of the run. A proof that resolving the
total costs exactly one RPC (not one per batch) already exists for the
posts leg at
`tests/test_cli_clone_sync.py::test_sync_with_nothing_to_copy_asks_for_no_approximate_total`
(the `CountingClient` pattern, ~line 3767); reuse that pattern for the
comments-phase call-count test below rather than inventing a new one.

- [ ] Test (`tests/test_clone_progress.py`): a new async test proving
      `resolve_total()` re-queries after `phase()` resets it — construct
      a fake `tg.get_messages` returning two different `total` values on
      successive calls (via a small counter/list closure, matching the
      style of the file's existing fakes), call `resolve_total` once,
      call `phase("comments")`, call `resolve_total` again, and assert
      the underlying fake was invoked twice and the second call's
      resolved total is reflected in the next `.batch()` line (not the
      first call's stale value, and not `~?`).
- [ ] Test (`tests/test_cli_clone_sync.py`): extend
      `test_sync_announces_the_comments_and_roster_phases` (or add a
      sibling next to it) so it asserts the batch line **immediately
      following** the `"[sync 123] 1/~1 · comments"` phase line reports
      a resolved numeric denominator (matching the fake's message count,
      as `CloneSyncClient.get_messages` already returns
      `SimpleNamespace(total=len(self.messages))` for the source entity)
      — not `~?`. Keep the existing final-line assertion for `roster`
      showing `~?` (roster never calls `copy_batch`/`resolve_total`, so
      it correctly has nothing to resolve against — this is not a bug
      and this plan does not touch it). Also add a call-counting
      assertion (reuse the `CountingClient` pattern from
      `test_sync_with_nothing_to_copy_asks_for_no_approximate_total`,
      ~line 3767) proving the comments phase spends exactly **one** extra
      `get_messages(entity, limit=0)` call total, not one per comment
      batch — this is the test that would catch a naive "always
      re-resolve" fix that reintroduces an RPC-per-batch regression.
- [ ] Implement: in `src/tgcli/clone/progress.py::SyncProgress.phase`,
      reset `self._total_resolved = False` alongside the existing
      `self._total = None`. Two lines total in the whole task:

      ```python
      def phase(self, name: str) -> None:
          self._write(f"{self._prefix()} · {name}")
          self._total = None
          self._total_resolved = False
      ```

      No other file changes: `resolve_total`'s existing lazy-on-first-batch
      guard and its `FloodWaitError` passthrough are unaffected and
      already correctly tested.
- [ ] No CONTRACT.md change for this task: `clone sync` progress lines
      are already documented in §2 as informative and non-contractual
      (`<total>` "the best-effort source message count (`?` when
      unavailable)"); the fix changes when `?` legitimately appears, not
      the documented shape.

### 4. Release

- [ ] Run `./scripts/gate.sh`; quote real output, not "tests pass".
- [ ] `CHANGELOG.md`: one new section naming ADR-0053 for the stdout
      envelope, plus a bullet noting the comments-phase progress
      denominator fix as a corollary (no separate ADR reference for that
      bullet — it is not one).
- [ ] Double version bump: `pyproject.toml` and `src/tgcli/__init__.py`,
      both to the next patch (`1.2.9` → `1.2.10`), same commit as the
      CONTRACT and CHANGELOG changes.
- [ ] `docs/MAP.md`: no module gained or lost a role (`output.py` and
      `clone/progress.py` keep their existing one-line descriptions) —
      confirm, do not edit unless review disagrees.
- [ ] DEVLOG entry per the template at the top of `docs/DEVLOG.md`.
- [ ] PR → `reviewer` subagent from the merge-base → green CI (PR-event
      run, not a stale push-event run) → merge → tag `v1.2.10` on the
      merge commit per `docs/agents/release.md`.
