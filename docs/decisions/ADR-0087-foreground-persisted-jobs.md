# ADR-0087: Foreground persisted jobs over typed checkpointed workloads

Date: 2026-08-10
Status: accepted (2026-08-10; owner grilling for #146)
Supersedes: [ADR-0070](ADR-0070-archive-refresh-scheduling.md) entirely.
Amends: [ADR-0068](ADR-0068-local-archive-store.md) by replacing `tg archive
refresh` with independently scheduled archive sync and transcription jobs.
ADR-0002's no-daemon rule, ADR-0062's explicit session roles, and ADR-0072's
account-wide request governor remain in force.

## Context

ADR-0072 deliberately turns broad archive work into multi-day, checkpointed
passes. The governor decides whether a Telegram request may run; it does not
decide which of several archive and clone workloads should spend the next
available quantum. Today that decision is encoded accidentally in separate
launchd commands. `archive refresh` also serializes Telegram sync and local
transcription even though they contend for different resources.

Issue #146 was owner-grilled after the governor shipped in 2.0.0. The owner
requires durable scheduling, priority, fairness, cancellation, retry state,
and restart recovery, but not a resident runtime or arbitrary shell jobs. The
process model stays foreground one-shots invoked by launchd. A named Telegram
session role keeps interactive use independent; the governor remains shared
by every role because Telegram limits the account, not the session file.

## Decision

### 1. `tg jobs` schedules four typed workloads, never shell commands

The v1 workload kinds are:

- `archive-backfill`: one archive dialog per quantum, explicit `CHAT ...` or
  the standing `--private` category;
- `archive-sync`: one complete changes difference/replay plus its bounded
  catch-up and media work per quantum;
- `clone-sync`: one existing 50-batch clone window per quantum;
- `archive-transcribe`: one ready local media item per quantum.

The public control surface is:

```text
tg jobs add archive-backfill --key KEY (CHAT ... | --private)
  [--limit N] [--priority {low,normal,high}] [--replace]
tg jobs add archive-sync --key KEY [--max-events N] [--max-dialogs N]
  [--max-media N] [--priority {low,normal,high}] [--replace]
tg jobs add clone-sync --key KEY SOURCE
  [--priority {low,normal,high}] [--replace]
tg jobs add archive-transcribe --key KEY [--max-attempts N]
  [--priority {low,normal,high}] [--replace]
tg jobs list
tg jobs show KEY
tg jobs cancel KEY
tg jobs run --lane {telegram,local}
tg jobs run --rearm KEY
```

Global `--account` selects the registry. Every `jobs run` requires global
`--max-runtime N`, with `1 <= N <= 3000`; the cap is cooperative and may
overrun by at most the current workload quantum. A Telegram lane additionally
requires an explicit non-primary `--session-role` (the documented convention
is `job`). It never falls back to primary. A local lane rejects a session role
because it opens no Telegram session.

Keys are operator-chosen stable identifiers. Adding the exact same spec while
its latest generation is queued or running is an idempotent no-op. Adding the
same terminal spec creates the next generation. A changed spec is exit 2
unless `--replace` is present; replacement atomically cancels a queued
generation and creates the next one, but refuses a running generation.

`jobs run --rearm KEY` is the safe recurring-launchd form. It creates a new
generation only when the latest one completed, runs an already-queued
generation as-is, and refuses failed or cancelled jobs. Therefore an outage or
an owner cancellation cannot be silently resurrected by the next timer wake.

### 2. The registry is account-scoped SQLite/WAL with bounded history

Each alias owns `~/.local/state/tgcli/jobs/<alias>/jobs.db` (or the selected
`TGCLI_STATE_DIR`), in a `0700` directory with database files at `0600`.
SQLite runs in WAL mode. The schema stores:

- metadata: schema version, alias, and nullable Telegram user id;
- immutable generations: key, generation, workload kind/lane, canonical typed
  spec JSON and hash, base priority, state, timestamps, `not_before`, failure
  streak, cancellation request, last result/error, and counters;
- append-only events: transition, quantum result, retry/defer, cancellation,
  crash recovery, and failure details.

Only the newest 200 events per key are retained. Completed jobs are never
auto-deleted. `tg store stats` reports the jobs DB/WAL/SHM bytes and state
counts but cleanup does not remove the registry.

Offline commands can create a registry before Telegram is available. The
first Telegram-lane run binds it to the live `get_me().id`; every later
Telegram run verifies that identity before taking work. An alias/user mismatch
is exit 2 and never merges histories.

### 3. Two process-lifetime lane locks provide crash recovery

There are exactly two lanes: `telegram` for archive backfill/sync and clone
sync, and `local` for archive transcription. One process per account/lane
holds an `flock` for its full `jobs run`; the two lanes may run concurrently.
There are no leases, heartbeats, worker PIDs in the contract, IPC, or resident
processes.

After acquiring a lane lock, a runner atomically returns stale `running` rows
in that lane to `queued` and records a crash-recovery event. If cancellation
had already been requested, recovery makes the row `cancelled` instead. The
underlying commands own their cursors and durable checkpoints, so replay after
a crash resumes rather than inventing a second progress store.

`jobs cancel` sets a durable cancellation request without taking the lane
lock. A queued job becomes cancelled immediately. A running job checks the
flag at safe boundaries and becomes cancelled after its current quantum; no
RPC, media transfer, or transcription subprocess is killed mid-operation.

### 4. Scheduling is priority plus bounded aging

Priorities are `high=2`, `normal=1`, and `low=0` (default `normal`). For each
eligible queued job:

```text
effective_priority = base_priority + min(skipped_quanta, 2)
```

The runner chooses the greatest effective priority, then the oldest
`last_run_at` (never-run first), then key lexicographically. The selected job
resets `skipped_quanta` to zero; every other eligible job increments it. This
lets a high-priority job win immediately while guaranteeing a continuously
eligible low-priority job reaches parity after two skipped quanta.

A runner loops until the wall-clock cap is reached or no job is currently
eligible. It never sleeps until `not_before`; launchd owns the next wake.
Each invocation returns aggregate lane counts plus per-selected-job outcomes.

### 5. Workloads return a uniform completion signal

The four command-owned results gain or expose `remaining: bool`. A generation
becomes `completed` only after a successful quantum returns
`remaining: false`. It remains queued after `remaining: true`, a normal
governor stop, or a rate limit. `not_before` carries a known retry time.
Archive backfill, archive sync, and clone sync gain the field; archive
transcription already has an equivalent queue result and standardizes its
name.

Adapters invoke the existing command functions in-process; they do not spawn
`tg` recursively and do not parse their stdout. Each adapter also exposes the
workload's existing durable progress token so progress made before a later
error resets the consecutive failure streak. Archive sync and transcription
gain cooperative deadline/cancellation checks at their existing durable
boundaries. A single slow quantum may overrun the cap; there is no hard kill.

Normal stops (`breadth_budget_exhausted`, `wall_clock_cap`, and
`cooldown_deferred`) and rate limits remain queued and do not consume a
runtime-failure attempt. A no-progress runtime failure retries after 5 minutes
and then 30 minutes. The third consecutive no-progress runtime failure enters
`failed`; its terminal event records a two-hour retry recommendation, and a
new generation created from that failed spec is not eligible before it.
Blocked, config/auth, and not-found errors enter `failed` immediately.

### 6. Safety and diagnostics stay at the public boundary

`jobs add`, `--replace`, and `jobs cancel` are local-state mutations blocked
by `--readonly` / `TGCLI_READONLY`. A local runner has the same local mutation
gate and is allowed under `TGCLI_NO_SEND`. A Telegram runner is blocked by all
three mutation gates (`--readonly`, `TGCLI_READONLY`, `TGCLI_NO_SEND`) before
config/session/network work, even when the selected archive workload only
reads Telegram. Clone mutations retain their existing per-RPC audit records;
the scheduler adds no duplicate mutation audit.

Detailed job specs, targets, keys, results, and errors are available from
`jobs list`, `jobs show`, registry events, and `jobs run --json`. The global
invocation journal records only the `jobs` command, lane, and aggregate
selected/completed/deferred/failed/cancelled counts. It never records a job
key, target, raw argv, spec, or exception text.

When a generation first enters `failed`, one best-effort macOS notification
contains only its key and `tg jobs show <key>`. It contains no account target,
message data, exception detail, or raw command. Notification failure cannot
change job state or command exit.

### 7. The old refresh composition is removed at cutover

`tg archive refresh`, its command/composition modules, its failure-streak
behavior, and `docs/assets/tgcli-archive-refresh.plist` are removed in the
campaign. There is no compatibility alias. Operators create an
`archive-sync` Telegram job and an `archive-transcribe` local job, driven by
checked-in launchd templates that call `jobs run --rearm KEY`. The independent
lane locks allow those stages to overlap safely.

## Rejected alternatives

- A session-owner runtime, daemon, IPC queue, or shared session file: adds a
  lifecycle and authorization-risk class that the required one-shot schedule
  does not need; ADR-0002 and ADR-0062 already define the safe process model.
- A parent process spawning arbitrary commands: loses typed validation,
  progress/checkpoint knowledge, result classification, and audit ownership.
- One mixed lane: local transcription would wait behind Telegram pacing and a
  Telegram session lock for no resource reason.
- Leases and heartbeats: a process-lifetime `flock` already answers liveness;
  command checkpoints make crash replay safe.
- Unbounded priority: starves maintenance work. Bounded aging keeps operator
  intent without requiring weights, quotas, or a second scheduler policy.
- Hard cancellation: killing inside an RPC or atomic media/state operation can
  leave an outcome unknown. Cancellation is cooperative at durable boundaries.

## Campaign and acceptance

Issue #146 is the scoped campaign map. Its three implementation slices are:

1. registry/control plane plus a working local transcription lane;
2. Telegram archive/clone adapters, uniform `remaining`, explicit role and
   safety gates;
3. recurring rearm, failure notification, refresh removal, launchd/docs, and
   integration hardening.

Every slice is TDD-first, runs the full gate, and receives an independent
whole-diff review before squash merge into the campaign integration branch.
The final integration PR receives the same head-SHA CI/review gate, then one
3.0.0 release carries the complete contract change.

Live acceptance performs no new Telegram publication: a real read-only
archive-sync quantum, a caught-up clone no-op, and one bounded real local
transcription quantum. If the available clone is not caught up or any other
Telegram mutation would be required, the run stops for fresh owner approval.

## Contract impact

This is a full-lane additive command family plus removal of the released
`archive refresh` command. `docs/CONTRACT.md`, README/SKILL/guide pages, MAP,
store inventory, invocation-journal fields, launchd assets, and release notes
change with the implementation. The removal is deliberate and breaking; this
ADR authorizes it without a compatibility period, and CONTRACT §3 therefore
requires the integration release to be 3.0.0.
