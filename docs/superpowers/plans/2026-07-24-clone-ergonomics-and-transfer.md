# Clone ergonomics (mute + folder) and parallel chunk transfer — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. Scope is fixed by
> [ADR-0046](../../decisions/ADR-0046-clone-destination-ergonomics.md),
> [ADR-0047](../../decisions/ADR-0047-clone-parallel-chunk-transfer.md) and
> [ADR-0048](../../decisions/ADR-0048-clone-poll-breakdown-vote.md).
> Execute **after** (or rebased on) the flood-containment plan
> (`2026-07-24-flood-containment.md`) — they touch the same files.
> Each ADR ships as its own tagged patch release (ADR-0038): ergonomics
> and transfer must be separate PRs.

**Goal:** Cloned peers arrive muted and filed into the Telegram folder
"Clone"; protected-channel reupload transfers file chunks with
parallelism 4 instead of one RPC at a time (measured 92% of sync wall
time).

**Non-goals:** parallel sends or parallel batches (ordering guarantees
stay); a parallelism flag or config key; prefetch pipeline (re-measure
after ADR-0047 lands; separate decision).

## Global constraints

- TDD; no network in tests; boundary tests assert exact Telethon
  request types and arguments (AGENTS.md).
- CONTRACT.md updates land in the same commit as the code.
- `scripts/gate.sh` before every commit; raise flagged ceilings to
  measured size only.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.

## Part A — ADR-0046 ergonomics (one PR, one patch release)

### A1. Mute tool-created peers

- [ ] Boundary tests: after commit init, exactly one
      `account.UpdateNotifySettingsRequest` per tool-created peer with a
      far-future `mute_until`; already-muted peer (fake reports muted) →
      no request; failure → warning on stderr, `ergonomics.muted: false`,
      exit still 0.
- [ ] Implement in the init commit path after the link step; idempotence
      via reading current notify settings.

### A2. "Clone" folder

- [ ] Boundary tests: `GetDialogFiltersRequest` read; existing filter
      titled `Clone` → `UpdateDialogFilterRequest` with peers appended
      (`folder: "added"`, or `"present"` when both already there); no
      filter → created with lowest free id; folder/peer limits or RPC
      failure → `folder: "unavailable"` + stderr warning, exit 0.
- [ ] Implement; both peers (destination + discussion when enabled).
- [ ] CONTRACT: `ergonomics` object in the init commit JSON example +
      prose; best-effort semantics documented.

### A3. Release

- [ ] CHANGELOG section at the next free patch version + bump in **both**
      `pyproject.toml` and `src/tgcli/__init__.py`; DEVLOG entry; PR →
      reviewer → CI → merge → tag.
- [ ] Live check: `tg clone init 3802378977` re-run — both икона peers
      end muted and in the "Clone" folder; Джарвис clone via its own
      re-init.

## Part B — ADR-0047 parallel chunk transfer (separate PR + release)

### B1. Striped download in reupload

- [ ] Extract/reuse the stride pattern from `media.py`
      (`_download_parallel`) as a shared helper (ADR-0043: shared seam,
      not a copy) usable by `_reupload_batch` for each media message.
- [ ] Tests: chunks written at correct offsets; single-chunk files take
      the sequential path; failure of one worker aborts the batch before
      any send.

### B2. Parallel part upload

- [ ] Boundary tests: N workers issue `SaveFilePartRequest` /
      `SaveBigFilePartRequest` for distinct `file_part` indices of one
      file; the finalizing `InputFile`/`InputFileBig` carries the correct
      part count; small files unaffected; FloodWait in any worker cancels
      siblings and surfaces exit 5 with cooldown armed.
- [ ] Implement with constant parallelism 4.
- [ ] Confirm ordering: sends remain sequential; cursor/state discipline
      untouched (existing tests must stay green unmodified).

### B3. Verify and release

- [ ] Re-measure: timestamped `-v` sync protocol (see
      `2026-07-24-clone-title-prefix.md` task 8) on a protected channel;
      record before/after phase table in DEVLOG with a verdict line.
      Baseline (2026-07-24, live): sample 1 — 43.9s/5 msgs, upload 56.6%,
      download 35.7%, send 2.7%; sample 2 — 590s, download 79.4%
      (GetFile × 2562), upload 18.9%, send 0.8%. Transfer share 92–98%.
- [ ] CHANGELOG + double version bump; PR → reviewer → CI → merge → tag.

## Part C — ADR-0048 poll breakdown (separate PR + release)

### C1. Honest placeholder (all paths)

- [ ] Test: poll with `total_voters > 0` and empty `results.results` →
      snapshot renders the "распределение по вариантам недоступно" line
      instead of `0% · 0` per option; polls with a breakdown render as
      today.
- [ ] Implement in `clone/snapshot.py`.

### C2. Transient vote capture

- [ ] Boundary tests: anonymous open non-quiz poll without breakdown →
      exactly one `SendVoteRequest` (cast), a results read, one
      `SendVoteRequest` with empty options (retract), audit records for
      both; snapshot numbers have the own vote subtracted (chosen option
      −1, total −1) and match the pre-vote totals.
- [ ] Tests for exclusions: public poll / quiz / closed poll → **zero**
      vote requests, placeholder or native results as available.
- [ ] Tests for gates: `--readonly` / `TGCLI_NO_SEND` → no vote, snapshot
      degrades to placeholder, sync continues.
- [ ] Test: retract failure → loud stderr warning + additive JSON marker
      in the sync report; sync does not abort.
- [ ] Implement; CONTRACT prose + sync JSON marker in the same commit.

### C3. Release

- [ ] CHANGELOG + double version bump; DEVLOG; PR → reviewer → CI →
      merge → tag.
- [ ] Live check on the икона discussion poll
      (destination message t.me/c/3514350021/31): re-sync after an edit
      or re-clone scenario per owner instruction; verify percentages
      appear and source poll totals are unchanged afterwards.
