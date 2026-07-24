# Clone flood containment — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. All work on branch `claude/flood-containment`.
> Scope is fixed by
> [ADR-0045](../../decisions/ADR-0045-clone-flood-containment.md);
> nothing beyond it.

**Goal:** After a FloodWait, no clone command of the same account can
hammer Telegram until the deadline passes (account-scoped cooldown); a
clone can be commissioned without its discussion-group peer
(`--no-comments`); the init preview shows peer-creation cost and current
flood posture before commit.

**Non-goals:** any sync speedup work; changing Telethon client settings
(`flood_sleep_threshold=0` / `request_retries=0` stay); pacing/sleeps
between sends; touching the roster exception (ADR-0024).

## Global constraints

- **TDD per task**; tests never touch the network or real state root.
- **One writer:** the account record is written via `tgcli/atomic.py`
  only (ADR-0043; `scripts/check-architecture.py` will flag `write_text`).
  Add the new module to `STATE_WRITER_MODULES`.
- **Contract discipline:** CONTRACT.md changes land in the same commit as
  the code that changes output.
- **Gate before every commit:** `scripts/gate.sh`. Raise any flagged
  ceiling to the measured size only.
- **Merge discipline:** PR → `reviewer` subagent → green CI (PR-event run
  on head SHA; a cancelled push-event duplicate is expected) → merge →
  tag per `docs/agents/release.md`.

## Tasks

### 1. Account flood record — new seam `src/tgcli/clone/flood.py`

Small module owning the per-account record
`clones/account-<account_user_id>.json`:
`{"cooldown_until": ISO|null, "last_peer_created_at": ISO|null}`.

- [x] Tests: arm/load roundtrip; expired deadline reads as None;
      corrupt/absent file reads as empty record (fail-open for reads);
      atomic write (no partial file on injected failure).
- [x] API: `load(account_user_id)`, `arm_cooldown(account_user_id,
      deadline)`, `record_peer_created(account_user_id, at)`,
      `cooldown_deadline(account_user_id) -> datetime | None`. Writes via
      `tgcli.atomic`; add `tgcli/clone/flood.py` to
      `STATE_WRITER_MODULES` in `scripts/check-architecture.py` (and its
      mirror in `tests/test_check_architecture.py`).

### 2. Wire the account gate into clone commands

Sites: `src/tgcli/commands/clone.py` — `_with_cooldown` (arms on
FloodWait), `_enforce_cooldown` (pre-flight), the `clone-init-create` /
discussion-create paths (peer timestamps).

- [x] Test: FloodWait raised while syncing clone A → subsequent
      `clone sync` of clone B (same account) exits 5 locally with
      `retry_after`, **zero** network calls.
- [x] Test: `clone init --commit` for source B under an active account
      cooldown exits 5 before any request.
- [x] Test: read-only surfaces (`clone list`, `clone status`, init
      preview) are not blocked by an active account cooldown.
- [x] Implement: `_with_cooldown` additionally arms the account record;
      `_enforce_cooldown` checks `max(per-clone, account)` deadline;
      `CreateChannelRequest` successes call `record_peer_created`.
- [x] Test (boundary): the roster path still arms nothing (extend the
      existing ADR-0024 test if needed).

### 3. `clone init --no-comments`

- [x] Tests: preview with the flag records the choice in the preview
      payload; commit produces `comments: "disabled"`, creates **one**
      peer, links nothing; `sync` on a disabled clone skips the comment
      phase and the discussion roster (no discussion requests at all);
      re-init with `--no-comments` over `comments: "enabled"` state exits
      2 (`PolicyError`); state roundtrip accepts `"disabled"`.
- [x] Implement: parser flag; preview payload field; commit branch;
      `clone/state.py` validation set gains `"disabled"`; sync guards.
- [x] CONTRACT: flag, the `"disabled"` value, sync-skip semantics.

### 4. Preview flood hints

- [x] Tests: preview JSON contains `peers_to_create` (2 for a
      commented source with no recorded destination; 1 with
      `--no-comments` or no discussion; 0 when destination recorded) and
      `account_flood` with `cooldown_until` / `last_peer_created_at` from
      the account record (nulls when absent).
- [x] Implement in the preview assembly only — no plain-output change.
- [x] CONTRACT: preview example + prose.

### 5. Release mechanics (ADR-0038)

- [x] `CHANGELOG.md`: new section at the next free patch version
      (1.2.3 if 1.2.2 has shipped by merge time) — Added
      (`--no-comments`, preview hints) + Fixed/Changed (account-scoped
      cooldown).
- [x] Version bump in **both** `pyproject.toml` and
      `src/tgcli/__init__.py` (same-day drift lesson).
- [x] DEVLOG entry.
- [ ] PR → reviewer → green CI → merge → tag per
      `docs/agents/release.md`.

### 6. Skill discipline note (docs, same PR)

- [x] `SKILL.md` (and `docs/guide/` clone page if it exists): peer
      budget — `init` creates 1–2 peers; at most ~one peer-creating init
      per account per day; exit 5 = wait out `retry_after` fully, never
      retry in a loop; preview's `peers_to_create`/`account_flood` is the
      pre-commit check.
