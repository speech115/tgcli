# ADR-0131: Test, script and hook decisions

Status: Accepted

## Context

These decisions about the test suite and its fakes, `scripts/`, Claude Code hooks and the gate used to live as code comments beside the
code they explain. ADR-0132 removes comments from code, so the non-obvious
ones move here, grouped by module and by the function, class or name they
sit on. Each entry is the reason for a choice that the code alone does not
show; the code is the authority for what it does.

## Decision

### `.claude/hooks/guard_session_files.py`

- `_runs_bare_python`: A wrapper (`env -S'python3 …'`, `sudo -E python3`) can hide the interpreter inside an option; any bare python word after it counts.

### `.githooks/pre-push`

- The static half of the gate runs before every push, so a lint or type error is caught in seconds instead of a CI round. Tests stay in CI and gate.sh. It checks the working tree, so uncommitted edits count too.

### `scripts/bench-clone.py`

- `main`: noqa: E402

### `scripts/gate.sh`

- `env TGCLI_READONLY=1 TGCLI_ACCOUNT=gate-isolation` around pytest: hostile shell values on purpose. A test must set whatever tgcli environment it needs, so a developer's exported TGCLI_READONLY never turns the gate red. CI's test job sets the same values.
- `pytest tests/live`: live smoke runs read-only against the real account in the caller's own environment and is skipped unless TGCLI_LIVE_SMOKE=1. CI has no Telegram session, so it does not run tests/live.

### `scripts/mutate.py`

- `changed_lines`: git diff never shows an untracked file: every line of one is new.
- `mutants`: AST columns are UTF-8 byte offsets; slice bytes, not characters.
- `run_suite`: An absolute PYTHONPATH, or the editable install shadows the copy and every mutant "survives".

### `scripts/repro.py`

- `run_tests`: An absolute PYTHONPATH, or the editable install shadows BASE's src.

### `tests/conftest.py`

- `FakeClient.add_event_handler`: Telethon 1.44 returns None here: removal must go by callback.
- `TRANSIENT_ERRORS`: "Ask again later", never "Telegram refused": one of each family the client boundary surfaces (tg_errors.TRANSIENT_ERRORS). Factories, so each test gets a fresh error; a flood of either kind is exit 5, the others an untranslated 1.
- FLOOD_PREMIUM_WAIT_X: the download/upload speed limit a non-Premium account meets. Another 420, never a refusal of the item.
- Telethon's own internal-retry family (RPC_CALL_FAIL, RPC_MCGET_FAIL, INTERDC_N_CALL_*) maps to ServerError subclasses; built from the wire so a Telethon upgrade that moves them out of ServerError fails here.

### `tests/fakes/clone.py`

- `CloneSyncClient`: Telethon restores the logged-in account id from the session on connect, so a connected client knows it without a get_me RPC.
- `CloneSyncClient.__init__`: Which history reads happened, as (leg, min_id): order is observed through what the destination receives, never through call flags.
- `CloneSyncClient.__init__`: Chats the source history response ships alongside a message: where Telegram puts ``ChannelForbidden`` for a quoted channel (quotes.py).
- `PHOTO_EXTENSIONS`: Telegram refuses an uploaded photo whose file name lacks an image extension (PHOTO_EXT_INVALID); the name comes from the InputFile, not the bytes.
- `CloneCommentsClient.get_messages`: Telegram fills a live discussion group with its own anchors for our posts; anything else must be planted explicitly.

### `tests/fakes/clone_faults.py`

- `_AS_BEFORE`: An after-fault on an operation that sends nothing is the matching before-fault: there is no answer to lose.
- `FaultyWorldClient.__call__`: What the group really holds: the fake would otherwise show every unplanted id as one of Telegram's anchors.

### `tests/fakes/clone_world.py`

- `worlds`: A sync records a copy and moves the cursor in one step, so every post at or below the cursor was copied; a lost source is an id gap.
- `worlds`: The comments leg stops before an anchor whose post the posts leg has not reached, so everything it already passed sits below such an anchor.
- `worlds`: Neighbouring posts without a reply may form an album, which the clone forwards in one request carrying several ids. Drawn last, so the album choice never reshapes the rest of the world.

### `tests/fakes/snapshot.py`

- `VOLATILE`: Values that differ on every run; the key stays so its presence is pinned.
- FTS5 bm25 score: varies with the SQLite build

### `tests/test_clone_benchmark.py`

- `SECONDS_CEILING`: Wall time is machine-dependent, so it is held only to a ceiling that a healthy run clears many times over (about a second on a laptop); request counts and state writes are the exact, deterministic cost.

### `tests/test_clone_reply_placement.py`

- The worlds the search shrank each past reply bug to, pinned so the lever keeps catching them whatever the search happens to draw. #313: with window 1 a later posts window walks parent post 2 as a group thread root, and group message 2 anchors post 1.
- #315: post 7 replies to comment 2, which is already in the destination group.
- #321: comment 2 quotes channel post 2, which never existed; its thread is known.

### `tests/test_clone_sync_faults.py`

- `plans`: The n-th operation of a run, and the n-th send: a run makes a few dozen operations and a handful of sends, so both counters reach every step.
- `CloneSyncFaultMachine.sync`: L2: a copy the clone lost track of is adopted when the leg's saved send intent names it; one it cannot explain stops the next copy into that chat cold, and the run says so (exit 2, manual repair).
- `CloneSyncFaultMachine.sync`: The fault that ended the run names the exit (CONTRACT §4): a flood of either kind is 5 with retry_after, a lost or failed request 1, a send Telegram did not confirm 2.
- `CloneSyncFaultMachine.finish`: No fault leaves a lasting mark: every copy is the one a run that never failed makes, down to its body and where its reply points.
- `SHARDS`: Separate tests let xdist spread the unchanged example budget across workers.
- `test_clone_sync_faults`: Explicit seeds distinguish factories with identical source under derandomize.

### `tests/test_command_classes.py`

- `READS`: Network reads: Telegram is only asked, never changed. Output files a read writes (`media download`, `export`) are the caller's own.

### `tests/test_properties.py`

- `TEXT`: Includes astral plane characters (emoji) on purpose: Telegram entity offsets count UTF-16 code units, so astral input is where offset bugs live.

## Consequences

A change that breaks one of these reasons updates or removes its entry in the
same PR. A new decision of this kind is added here, not as a code comment.
