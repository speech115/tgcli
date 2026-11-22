---
name: verify-concurrency-models
description: Run and maintain the exhaustive BFS formal verification models for tgcli's login/setup promotion and preview-to-commit concurrency state machines. Use before editing login_state.py, commands/login.py, commands/setup.py, safety.py, preview_commit.py, commands/send.py, commands/mutate.py, or chatref.py.
---

# Verify concurrency models

Exhaustive BFS state-space models check every reachable interleaving of two
concurrent state machines where real bugs have repeatedly occurred in `tgcli`.

## Coverage record

Sync commit: `a5afb1e` (main)

1. **Login / `tg setup` promotion & cancel model** (`tests/models/concurrency_models.py:verify_login_promote`)
   - `src/tgcli/login_state.py`: `promote`
   - `src/tgcli/commands/login.py`: `continue_login`
   - `src/tgcli/commands/setup.py`: `SetupFlow._finish_authorized`, `_cancel`, `_watch_qr`
   - Bounds: 2 concurrent actors (`P1` = `tg setup` with QR cancel, `P2` = `accounts login`) racing on one new alias, one session lock, one backup slot, and one config entry.
   - Properties:
     - `CommittedSessionIntact`: once an alias is written to `config.toml`, its session file on disk belongs to that committed login across all rollbacks and concurrent failures.
     - `CancelledNeverCommits`: a cancelled `tg setup` run never promotes a session or writes `config.toml`.
     - `LockReleasedAtQuiescence`: once both actors finish, no session lock remains held.

2. **Preview → commit handshake model** (`tests/models/concurrency_models.py:verify_preview_commit`)
   - `src/tgcli/safety.py`: `begin_commit`, `mark_attempted`, `was_attempted`, `finish_commit`, `release_commit_locks`
   - `src/tgcli/preview_commit.py`: `prepare`, `dispatch`, `finish`
   - `src/tgcli/commands/send.py`: `commit`, `commit_forward`
   - `src/tgcli/commands/mutate.py`: `commit_edit`, `commit_delete`
   - `src/tgcli/chatref.py`: `resolve_input`
   - Bounds: 2 concurrent workers on one `preview_id`, plus asynchronous TTL expiry, source-message edit, target-peer drift, and lost Telegram reply.
   - Properties:
     - `AtMostOnceDelivery`: Telegram delivers at most one message per preview across concurrent workers, lost replies, and retries.
     - `ExactIntent`: every delivered message targets the pinned preview peer and matches the previewed content.
     - `NoStickyPendingAfterExpiry`: an expired `.pending` preview reverts to `.json` and clears `.attempted` on the next `begin_commit`.
     - `RetryRecoversAfterLostReply`: a retry after a lost reply is never blocked by a post-attempt source edit so `random_id` deduplication still runs.
     - `NoLeakedFlock`: when no worker is inside a commit, the preview lock is free.

What is left out: crash-induced disk corruption mid-`os.replace`, kernel flock failure, and multi-preview interactions (covered separately by `tests/test_safety.py`, `tests/test_atomic.py`, and `tests/test_cli_clone_init.py`).

## When to run

Check whether the diff touches any file listed in the Coverage record:

```bash
git diff --name-only a5afb1e..HEAD -- \
  src/tgcli/login_state.py src/tgcli/commands/login.py src/tgcli/commands/setup.py \
  src/tgcli/safety.py src/tgcli/preview_commit.py src/tgcli/commands/send.py \
  src/tgcli/commands/mutate.py src/tgcli/chatref.py
```

Run the models and real-filesystem replays:

```bash
uv run pytest -q tests/test_concurrency_models.py
```

This also runs automatically in `./scripts/gate.sh` and GitHub Actions CI.

## Reading a failure

- **Violation on the clean model (`bug=None`)**: the failure prints the violated invariant name and the exact shortest sequence of atomic steps (`trace`). Replay that trace against the real Python code first:
  - If the real code exhibits the same bad outcome, you found a real concurrency or retry bug — fix the code, never weaken the invariant.
  - If the real code already prevents one of those steps via a new guard, update the model's transition so it matches the real atomic step, add a past-bug toggle proving the model fails without that guard, and advance `Sync commit` above after confirming the match.
- **Failure on a `*_catches_past_bugs` test**: a model change broke regression sensitivity for a historical bug (PR #217, #404, #504, #515, #516, #518). Fix the model before trusting a clean pass.
