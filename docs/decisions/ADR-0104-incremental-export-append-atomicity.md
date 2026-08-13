# ADR-0104: Incremental export publishes each append batch atomically

Date: 2026-08-13
Status: accepted
Form: full (released command behavior and `CONTRACT.md` §7 semantics)
Amends: [ADR-0032](ADR-0032-data-plumbing-inbox-ergonomics.md) decision 3
Closes: thermos audit T21

## Context

ADR-0032 added `export messages --append` / `--resume` but guaranteed atomic
replacement only for full exports. Incremental exports opened the destination
in append mode and wrote each JSONL row directly. If iteration, serialization,
or the process stopped after one row of a batch, the destination exposed that
partial batch. A later `--resume` then treated its last surviving row as the
committed cursor, so retrying could silently skip the rest of the failed batch.

The existing full-export path already streams through a same-directory
temporary file, but it started from an empty file. Loading an entire existing
export into memory for `atomic.replace_text` would discard that streaming
property, while introducing a checkpoint sidecar would add another durable
record whose ordering and recovery rules must agree with the JSONL file.

## Decision

1. `_atomic_text_destination` can seed its sibling temporary file with a
   byte-for-byte copy of the existing destination. `--append` and `--resume`
   use that mode; full message and subscriber exports continue to start from
   an empty temporary file.
2. New message rows stream only into the temporary file. After the complete
   batch succeeds, tgcli flushes and fsyncs the file, atomically replaces the
   destination with `os.replace`, then uses the shared atomic helper to fsync
   the parent directory. Iteration, write, fsync, or cancellation failure
   before replacement leaves the previous destination unchanged.
3. No checkpoint sidecar is introduced. `--resume` continues to derive
   `after_id` from the last non-empty row of the last successfully published
   destination. A failed append to a missing destination leaves it missing.
4. Atomic append requires a same-filesystem temporary copy of the existing
   output. The cost is one sequential read/write of the existing file and
   temporary free space for the old file plus the new batch.

## Rejected alternatives

- **Append in place with a checkpoint sidecar.** A sidecar cannot prevent a
  torn JSONL write by itself. Recovery would also need a durable byte offset,
  truncate ordering, stale-sidecar detection, and another persistent schema.
- **Record the original length and truncate on exceptions.** Exception cleanup
  does not run after `SIGKILL`, host loss, or power loss, which are the failures
  this decision must survive.
- **Build the complete output in memory and call `atomic.replace_text`.** This
  would make memory use proportional to an unbounded export and regress the
  existing streaming behavior.

## Contract impact

`docs/CONTRACT.md` §7 now guarantees that incremental append/resume publishes
one complete batch atomically: before replacement readers see the previous
file, and after replacement they see the previous rows plus the complete new
batch. Flags, JSON/TSV shapes, and exit codes are unchanged. Because this
strengthens released command semantics, the integrator must release it under
ADR-0038; this feature branch does not edit version files or `CHANGELOG.md`.
