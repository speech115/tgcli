# ADR-0089: Explicit crash-recoverable archive peer purge

Date: 2026-08-12
Status: accepted (2026-08-12; owner grilling for #198)
Amends: [ADR-0068](ADR-0068-local-archive-store.md) by implementing its
explicit per-dialog purge boundary.

## Context

`tg archive remove CHAT` removes a group or channel from future explicit
scope, but the archive deliberately retains its existing messages, revisions,
tombstones, transcripts, search rows, checkpoints, and media. That is correct
for a non-destructive scope command, but it left no way to honour an explicit
request to erase one archived dialog. Issue #198 confirmed 8.9k messages and
their media remained after two test scopes were removed.

SQLite can delete all peer rows transactionally, but SQLite and the filesystem
cannot share one atomic commit. Archive jobs or another session-backed archive
command can also restore files or rows while deletion is in progress. The
contract therefore needs an honest recovery state and an exclusion boundary,
not a claim of global atomicity.

## Decision

### 1. Purge is a separate offline command with an explicit commit gate

Add `tg archive purge CHAT [--confirm]`. `remove` remains non-destructive and
unchanged. `purge` resolves only durable `scope` / `sync_state` identity and
never opens Telegram. It accepts groups and channels; private 1:1 dialogs stay
the standing category and are rejected.

Without `--confirm`, the command reports exact current row counts plus the
number and bytes of peer media and resumable download files. It changes no
archive data and is allowed under readonly mode. `--confirm` is blocked by
readonly mode before configuration or state access and writes a redacted
`archive-purge` audit record before destructive work.

### 2. Commit is quarantine, one SQLite transaction, then cleanup

For one peer, the confirmed command:

1. writes an atomic versioned recovery marker under the account archive;
2. renames the controlled `media/<peer_id>/` directory and matching resumable
   download records into an account-local quarantine;
3. in one SQLite transaction deletes the peer from FTS, transcripts,
   revisions, tombstones, messages, `sync_state`, and `scope`; removes its
   channel subscription from the changes cursor; clears its scoped gap; and
   removes it from the last reconcile sample;
4. deletes the quarantine and recovery marker.

The marker retains only peer identity, aggregate counts, and controlled
checkpoint filenames — never message bodies. A retry using the same stored
username or peer id can find the marker even after SQLite identity rows are
gone.

If the database step fails after quarantine, the marker and files remain and
the database transaction rolls back; retry completes both steps. If final
filesystem cleanup fails, SQLite remains purged, JSON reports
`cleanup_pending: true`, and the command exits 1; retry finishes cleanup.
Absence is never reported as successful cleanup while a marker remains.

### 3. Work that can restore the peer is excluded

The confirmed command takes both jobs lane locks and refuses every queued or
running `archive-*` job by key, telling the operator to cancel it first. It
also takes every primary/named session lock for the selected account, so a
manual session-backed `archive add`, `backfill`, `sync`, or `rebaseline`
cannot overlap the destructive window. Another purge is excluded by an
account-local lock. Audit and completed/cancelled job history are retained.

## Rejected alternatives

- Make `archive remove` destructive: a scope edit must not silently erase the
  archive's keep-forever history.
- Purge private dialogs: the standing private category would immediately make
  the meaning ambiguous without a persistent denylist, which is not approved.
- Delete files after SQLite without quarantine: a crash would lose the only
  inventory capable of resuming cleanup by username.
- Promise one atomic SQLite/filesystem deletion: no such transaction exists.
- Cancel jobs automatically: cancellation is an operator decision and running
  jobs stop only at their own durable boundary.

## Consequences

- `archive/purge.py` owns preview inventory, exclusion, recovery markers,
  quarantine, peer-row deletion, and cleanup recovery. Archive schema v7 does
  not change.
- The new command is a released local mutation and requires a patch release.
- Real archive deletion remains a separate owner action after inspecting the
  preview; tests and acceptance use temporary stores only.

## Contract impact

`docs/CONTRACT.md` §13 gains `archive purge CHAT [--confirm]`, its JSON/plain
shape, readonly and failure semantics. Existing `archive remove` behavior and
all Telegram RPC contracts are unchanged.
