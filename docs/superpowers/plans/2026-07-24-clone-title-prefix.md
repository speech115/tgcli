# Clone title prefix + reupload speed measurement — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`) syntax
> for tracking. Tasks 1–5 are code/docs work on this branch
> (`claude/clone-title-prefix`). Tasks 6–9 are a live runbook: they need the
> owner's session and run only after the PR is merged.

**Goal:** Tool-created clone peers (destination channel and its discussion
group) are visibly titled `[Clone] {name}` so the owner can tell a clone
from its source in the dialog list. Then use the freshly commissioned
`[икона]` clone as the measurement bed for the clone-sync speed question
(phase breakdown of the reupload path). Scope is fixed by
[ADR-0044](../../decisions/ADR-0044-clone-title-prefix.md).

**Non-goals (explicitly out of scope):** any speedup implementation
(parallel download/upload, prefetch pipeline, striped reupload). Those are
a *separate future decision* taken only after Task 8's numbers exist.
Do not touch `transport.py`, `batching.py`, or media code.

## Global constraints

- **ADR-0044 is the approved scope; nothing beyond it.** The prefix is the
  constant `"[Clone] "`; no flag, no config key.
- `CloneState.source_title` stays unprefixed. No state-schema change, no
  migration.
- **Contract discipline (AGENTS.md):** CONTRACT.md updates land in the same
  commit as the code that changes output.
- **TDD per task:** failing test → minimal code → green. Tests never touch
  the network or the real state root (`TGCLI_STATE_DIR`/`TGCLI_CONFIG` →
  `tmp_path`).
- **Gate before every commit:** `scripts/gate.sh` (pytest, ruff check,
  ruff format --check, pyright, architecture check). If
  `scripts/check-architecture.py` flags a ceiling, raise it to the measured
  size only, in its own line of the PR description.
- **Merge discipline (AGENTS.md):** PR from this branch, `reviewer`
  subagent review, wait for green CI, then merge.

## Tasks

### 1. Helper seam — `attribution.destination_title`

- [x] Test in `tests/` (next to existing attribution tests):
      `destination_title("Джарвис ⚔ ИИздец") == "[Clone] Джарвис ⚔ ИИздец"`.
- [x] Implement in `src/tgcli/clone/attribution.py` (lives beside
      `display_name`, the other naming rule):

      ```python
      def destination_title(title: str) -> str:
          """Visible tool marking on tool-created peers (ADR-0044)."""
          return f"[Clone] {title}"
      ```

### 2. Destination channel rename uses the derived title

Site: `src/tgcli/commands/clone.py` — the `EditTitleRequest` block after
destination creation/adoption (currently compares
`destination.title != clone_state.source_title`).

- [x] Update `tests/test_cli_clone_init.py`: init renames the created
      channel to `[Clone] Source` (assert the exact `EditTitleRequest`
      title argument — boundary-test rule).
- [x] New idempotence test: destination already titled `[Clone] Source` →
      re-init performs **no** `EditTitleRequest`.
- [x] Implement: compare against and rename to
      `attribution.destination_title(clone_state.source_title)`; keep the
      `clone-init-title` audit unchanged.
- [x] Check the JSON assembly right below (destination `title` fields use
      `getattr(destination, "title", ...)` fallbacks): fallbacks must also
      go through `destination_title(...)` so JSON never reports an
      unprefixed title for a tool-created peer.

### 3. Discussion group rename uses the derived title

Site: `src/tgcli/commands/clone.py`, `_init_discussion` — currently
`title = attribution.display_name(source_group)` then compare-and-edit.

- [x] Update/extend the discussion init tests the same way (exact title in
      `EditTitleRequest`, idempotent re-run).
- [x] Implement: `title = attribution.destination_title(attribution.display_name(source_group))`.

### 4. CONTRACT.md

- [x] `clone init` commit example: `"destination":{"id":999,"title":"[Clone] Source"}`.
- [x] `clone sync` example: same destination title change.
- [x] Prose "init applies the source title/display name" → states the
      `[Clone] ` prefix on tool-created peers (destination + discussion),
      with `source.title` explicitly unprefixed.
- [x] Discussion section ("title/about/avatar are copied from the source
      discussion group") → title is copied *with the prefix*.

### 5. Release mechanics (ADR-0038)

- [x] `CHANGELOG.md`: new `1.2.1` section (Added/Changed as appropriate)
      in the existing format.
- [x] `pyproject.toml`: version `1.2.0` → `1.2.1`.
- [x] DEVLOG entry for the implementation session.
- [x] PR → reviewer subagent → green CI → merge → tag `v1.2.1` per
      `docs/agents/release.md`.

## Live runbook (owner session; after merge)

> Every command below talks to live Telegram. FLOOD_WAIT risk is accepted
> by the owner for today (second created peer of the day — ADR-0023
> observed 23s–349s waits on this path, worst case ~15h). If `clone` exits
> 5 with `retry_after`, stop and wait it out — never retry in a loop.

### 6. Retro-rename the Джарвис clone

- [ ] `tg --json clone init 3937334840` — idempotent re-run; expect exactly
      one `EditTitleRequest` (destination → `[Clone] Джарвис ⚔ ИИздец`),
      zero created peers.
- [ ] Verify in `tg --json clone list` / dialog list.

### 7. Commission the `[икона]` clone

- [ ] `tg --json clone init <икона>` — **preview**: confirm
      `"protected": true` (owner has confirmed; verify anyway) and note
      `approximate_message_count`.
- [ ] Commit the preview. The clone is born titled `[Clone] [икона]`.

### 8. Measure the reupload path (пункт 0 of the speed question)

- [ ] Run the first sync slice with verbose RPC logging, stderr
      timestamped, stdout (JSON) kept clean:

      ```bash
      tg -v --json clone sync <ICON_SOURCE_ID> --limit 5 \
        2> >(python3 -u -c 'import sys,time
      for line in sys.stdin: sys.stdout.write(f"{time.time():.3f} {line}")' \
        > ~/icon-sync-timing-1.log)
      ```

- [ ] Extract the phase breakdown from the log: timestamp deltas around
      download (`upload.GetFile` chains), upload (`SaveFilePart`/
      `SaveBigFilePart`, `UploadMedia`), and send (`SendMedia`/
      `SendMultiMedia`) requests, per message. Percent shares of wall time.
- [ ] Repeat once (`--limit 5` again → next 5 batches) for a second sample.

### 9. Finish and record

- [ ] Sync the икона clone to completion (`tg --json clone sync` without
      `--limit` until `"more": false`). It stays as a live clone.
- [ ] DEVLOG entry with the measured breakdown (redact live details per
      AGENTS.md rule) and an explicit verdict line: which of
      {parallel download/upload, prefetch pipeline, striped large-file
      download} the numbers justify — or "none; economics fine". That
      verdict is the input to a *future* ADR, not license to implement.
