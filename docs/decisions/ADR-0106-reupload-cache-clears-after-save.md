# ADR-0106: The reupload cache clears after the mapping save, not the send

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)
Amends: [ADR-0052](ADR-0052-clone-short-flood-wait-and-media-reuse.md) decision
7 (the reupload media cache survives a failed batch)

## Context

Thermos audit ticket T12. `_reupload_batch` `rmtree`d the per-clone download
cache right after Telegram accepted the send, before `_forward_batch` called
`topics.confirmed_destination_ids` and `state.save`. Telegram accepting the
RPC is not the same as the batch being durably recorded: a confirmation
Telegram never completes (an unmatched `random_id`, a short `updates` list) or
a crash between the send and the save left the mapping unsaved while the
downloaded bytes — up to the size of the batch's media — were already gone.
The next run then had to re-download them, which is exactly what ADR-0052's
cache exists to avoid.

## Decision

`_reupload_batch` no longer clears its cache. `_forward_batch` clears the
per-clone cache (`reupload.cache_dir(clone_state)`) itself, once, right after
`state.save(clone_state)` succeeds, and only when the batch actually used the
reupload transport (`mode == "reuploaded"`). `FloodWait` before that point
already left the cache alone (ADR-0052); this closes the remaining gap
between a confirmed-but-unsaved send and the save.

## Rejected alternatives

- **Clearing the cache from inside `_reupload_batch` but after an outer
  confirm+save callback.** Would hand `_reupload_batch` a callback whose only
  job is to run code that already lives three lines below its own call site
  in `_forward_batch` — a new abstraction for no new behavior.
- **A `try`/`finally` around the confirm+save that reraises.** The cache must
  survive the failure, not just be handled gracefully; `finally` would still
  delete it before the exception looked any different to the caller.

## Contract impact

None: no CLI flag, JSON shape, or exit code changes. The on-disk cache
directory's lifetime tightens to match the durability ADR-0052 already
promised.
