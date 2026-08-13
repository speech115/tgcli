# ADR-0091: `media download` never publishes incomplete or unsynced bytes

Date: 2026-08-13
Status: accepted
Form: full (safety behavior — a mutation path's own correctness guarantee)
Mirrors: [ADR-0083](ADR-0083-a-flood-must-not-destroy-finished-work.md) decisions
1 and 3, onto `commands/media.py`'s serial and parallel download loops
Closes: thermos audit T03

## Context

ADR-0083 fixed clone reupload's download so a `FloodWait` (or any other stop)
can never throw away finished bytes, and so a short stream can never be
published as if it were complete. `commands/media.py`'s own `tg media
download` predates that fix and shares both defects, unaddressed by ADR-0083's
own "Contract impact" section, which flagged the weaker resume record as a
follow-up rather than fixing it there:

1. **Serial loop, no completeness check.** After the `async for` loop over
   `tg.iter_download` exits — whether it ran out of chunks because the
   transfer finished or because the stream ended early with no exception —
   `download_media` calls `_publish(part_path, destination)` unconditionally.
   A server that closes the connection a few chunks short of `target.size`
   (no `FloodWaitError`, no `OSError`, just an exhausted async generator)
   gets published under the final name and reported `exit 0`.
2. **Serial checkpoint flushes without fsyncing.** Every `CHECKPOINT_EVERY_CHUNKS`
   chunks the loop called `handle.flush()` then wrote the `.json` sidecar
   through `atomic.replace_text`. `flush()` only moves the data out of
   Python's buffer into the OS page cache; without `fsync` first, a power
   loss between the flush and the (already-fsynced) sidecar write can leave
   a record claiming bytes the `.part` file does not have on disk, and the
   next run resumes onto a hole (the exact defect ADR-0083 named and fixed
   for `transfer.download_resumable`'s `_record` helper).
3. **The parallel path trusts `download_striped`'s pre-allocated size.**
   `download_striped` truncates the destination to `size` before writing a
   single byte, so the file already reports the full length regardless of
   how many bytes actually landed. If every worker's `iter_download` ends
   without raising — the same "connection closed early, no exception" shape
   as (1) — `_run_workers` returns normally, and `_download_parallel` publishes
   a full-size *sparse* file with no error. This is the identical shape
   ADR-0083 decision 3 already named for clone's reupload download before
   ADR-0083 moved that leg onto the serial, checkpointed `download_resumable`
   instead; `media download --parallel` still calls the shared
   `transfer.download_striped`, so it still carries the defect.

## Decision

1. **The serial loop requires `current == size` before `_publish`.** After
   the `with part_path.open(...)` block closes, `download_media` checks
   `target.size is not None and current != target.size` and raises
   `PolicyError` (`stopped at {current}/{target.size} bytes`) instead of
   publishing. The checkpoint already made `current` durable, so the partial
   file and its state sidecar are left in place — the next invocation resumes
   from exactly where the stream stopped, the same recovery `--parallel`
   downloads and clone reupload already have for a raised exception. A
   `target.size` of `None` (Telegram gave no countable size) skips the check;
   nothing to compare against.
2. **The serial checkpoint fsyncs before it writes the sidecar.** A new
   `_checkpoint` helper wraps `handle.flush()` with `os.fsync(handle.fileno())`
   ahead of `_write_state`, mirroring `transfer._record`'s ordering exactly.
   All three call sites (periodic, exception unwind, final) go through it.
3. **`transfer.download_striped` verifies its own postcondition.** After
   `_run_workers` returns without raising, `downloaded != size` now raises
   `RuntimeError(f"striped download ended at {downloaded}/{size} bytes")`,
   caught by the function's own `except BaseException` clause that already
   unlinks the destination — the same cleanup a worker exception gets. The
   check lives in `transfer.py`, not at the `media.py` call site: the
   pre-allocated file's on-disk size cannot prove completeness (that is
   exactly the trap), so only the function that holds the real byte counter
   can verify it. `_download_parallel` gets the guarantee for free and needs
   no new code — a `download_striped` failure means `_publish` is never
   reached, `state_path` is left with `"resumable": false` (unchanged
   behavior on any striped failure), and the next run restarts from zero
   through the existing "unresumable, discard both files" path.

## Rejected alternatives

- **Route `media download` through `download_resumable` (T31).** Absorbs
  this fix along with the media-identity gap ADR-0083's own "Contract
  impact" section named, but it is a bigger change to a released command's
  on-disk state format and its own decision, not this one. This ADR keeps
  the regression tests in place per the ticket's own note; a future
  `download_resumable` migration inherits them.
- **Check completeness in `media.py` after `download_striped` returns, by
  reading the file's size on disk.** Rejected: `download_striped` already
  `truncate(size)`s the file before the transfer starts, so the on-disk size
  is always `size` whether or not the bytes are real. Only the internal
  `downloaded` counter can tell the difference, which is why the check has
  to live inside `transfer.py`.
- **Raise a `PolicyError` from inside `transfer.download_striped`.**
  `transfer.py` has no dependency on `tgcli.errors` today (Telethon-facing
  code, plain-Python exceptions only, matching `upload_parts`'s existing
  `RuntimeError` for its own postcondition failure). Adding that import for
  one raise site is not worth a new module coupling; the CLI's generic
  `except Exception` arm already turns any untranslated failure into a
  `RUNTIME` / exit 1 envelope, consistent with `upload_parts`'s own failure
  shape.

## Contract impact

`docs/CONTRACT.md`: a `media download` (serial or `--parallel`) whose stream
ends early now exits nonzero instead of silently succeeding with a short or
sparse file. The serial case is `PolicyError` (exit 2, `BLOCKED`) and keeps
its partial file resumable; the parallel case is the existing striped-failure
shape (exit 1, `RUNTIME`) and restarts from zero on the next run, both
already-documented behaviors for a failed transfer — only the trigger
(silent short stream, not just an explicit error) is new.
