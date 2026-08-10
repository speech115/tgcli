# ADR-0083: A flood must not destroy finished work

Date: 2026-08-10
Status: accepted
Form: full (safety behavior + FloodWait handling)
Supersedes: [ADR-0047](ADR-0047-clone-parallel-chunk-transfer.md)'s **download**
leg for clone reupload (its parallel part *upload* is untouched)
Closes: #169, #170

## Context

ADR-0072 is deliberate about what a `FloodWait` means: the run stops, the
cooldown arms from the server's own `retry_after`, and nothing retries into a
live penalty. That posture is right and is not what these two reports are
about. They are about what the stop *costs*.

**#169.** A `clone sync` of a channel with a 55.9 MB protected video looped
for fifteen minutes across seven invocations, each one printing the identical
`download 8.0/55.9 MB (14%)` before drawing `FLOOD_WAIT` on
`upload.GetFileRequest` and exiting 5. `download_for_reupload` unlinks the
partial file before every download, so each run restarted from byte 0 and the
sync could never finish — for any protected file larger than what fits in one
run's flood budget, the clone was permanently stuck with no error saying so.
The download also ran `CLONE_TRANSFER_PARALLEL = 4` striped workers, which is
what reached the media limit ~17 requests in.

**#170.** `clone init --commit` on a channel with comments floods on
`channels.SetDiscussionGroupRequest`. The destination channel is already
created; the discussion group is not linked. `preflight` consumed the preview
with `consume_preview` — spent before any work — so retrying the same commit
answered `BLOCKED: preview is already used or does not exist`, which reads as
"nothing happened" while a real channel sits in the account. `clone sync` then
refused with `re-run clone init before syncing` and `clone status` showed a
healthy-looking clone. The recovery (a second `init` + commit) was found by
trial and error.

Both are the same shape: work that succeeded was thrown away because
something later in the same run hit a limit.

## Decision

1. **The reupload download resumes across runs.** `transfer.py` grows
   `download_resumable`: a serial `iter_download` loop that appends into a
   `.part` from a given offset and calls a caller-supplied `checkpoint` every
   8 chunks *and* on the way out, whether it finished or raised.
   `clone/reupload.py` owns the policy — a `<part>.offset` sidecar recording
   `{size, media_id, offset}` — and resumes only when the recorded size *and*
   Telegram's own document/photo id both still match, with the offset inside
   the file on disk. Anything else drops both and starts at zero. Size alone
   was not enough: re-exporting a video with the same settings yields the same
   byte length, and the resume would have spliced a new tail onto an old head
   into a file that passes every length check and is neither revision (review
   finding). The part file is `fsync`ed before each checkpoint, because the
   sidecar is written through `atomic.replace_text`, which fsyncs itself — a
   record must never outlive the bytes it claims. A checkpoint that fails
   while an exception is unwinding is swallowed: bookkeeping must not replace
   a `FloodWait` and take its `retry_after` with it.

2. **The reupload download is serial, not striped.** A parallel transfer
   writes its stripes at scattered offsets, so no byte count describes what it
   already has — `media download` states exactly this and refuses to resume a
   parallel transfer. A download that must make progress *across* runs
   therefore cannot be striped, and ADR-0047's throughput argument does not
   apply to a transfer that never completes. Reupload's parallel part
   **upload** keeps `CLONE_TRANSFER_PARALLEL = 4`.

3. **A short stream never publishes the final name.** `download_resumable`
   returns the byte count; a result short of the expected size raises and
   keeps the `.part` for the next run. The striped path pre-allocated with
   `truncate(size)`, so a truncated download left a full-size *sparse* file
   that only the `.done` marker kept out of the upload.

4. **Clone commits use the begin/finish preview handshake.**
   `clone init --commit` and `clone refresh --commit` move from
   `consume_preview` to `begin_commit(expected_kind=…)` plus `finish_commit`
   after a successful run — the mechanism `send`/`draft` have used since
   ADR-0028. A commit that fails partway leaves the preview `.pending` and
   retryable within its TTL; every step of `commit_init` is already idempotent
   (it adopts the destination and marker it previously created), so the retry
   links what is missing and creates nothing twice.

5. **A half-initialized clone says so.** `clone status` entries carry
   `discussion_linked`, and `status` prints a stderr line naming the exact
   recovery command for any clone with `comments: "enabled"` and no linked
   group. `clone sync`'s refusal names the same command instead of saying
   "re-run clone init".

## Rejected alternatives

- **Sleeping off a short media flood inside the run.** `retry_after` was 1 s
  and Telethon would absorb it by default. ADR-0072 sets
  `flood_sleep_threshold=0` precisely so a limit is never silently absorbed,
  and the incident that ADR answers was an agent retrying into a live penalty.
  Resume gets the file across the line without touching that posture.
- **Pacing every media chunk.** The MEDIA interval is 3 s and charged per
  file; charging it per 512 KB chunk would put a 55.9 MB file at over five
  hours. Choosing a smaller per-chunk interval would be a guess with no
  measurement behind it.
- **Verifying resumed bytes by content hash.** Telegram serves no whole-file
  hash to compare against, so this would mean re-reading the prefix over the
  network — the cost the resume exists to avoid. The media id changes whenever
  the file does, which is the property actually needed.
- **Resumable striping via a completed-stripe bitmap.** A second on-disk
  format and a new failure mode, to keep a parallelism that is what draws the
  flood.
- **Extending the preview TTL for a `.pending` clone commit.** The handshake
  alone fixes the reported dead end; a preview that outlives its five minutes
  is a separate decision about how stale a plan may be.
- **Making `clone init` detect and finish a half-built clone by itself.** It
  already does, on the *next* init — the gap was that the preview could not be
  reused and nothing said the clone was half-built.

## Contract impact

`commands/media.py`'s own resume record identifies its partial file by
`chat:message_id` and destination path only — weaker than what this ADR now
requires of clone, and the same splice is possible there. It is reported as a
follow-up rather than changed here: adding media identity to `media download`
alters the on-disk state format of a released command and is its own decision.

`docs/CONTRACT.md` §11: `clone init --commit` and `clone refresh --commit`
spend the preview on success rather than on acceptance, so a failed commit may
be retried with the same `PREVIEW_ID` while it is unexpired; `clone status`
entries gain `discussion_linked`; `clone sync`'s unlinked-discussion refusal
names the recovery command. The reupload download's on-disk cache gains
`<part>.offset` sidecars under the clone media cache directory, reaped with
the cache by `store cleanup`.
