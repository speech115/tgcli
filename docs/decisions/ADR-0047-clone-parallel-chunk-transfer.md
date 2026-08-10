# ADR-0047: Clone reupload parallel chunk transfer

Date: 2026-07-24
Status: accepted; the reupload **download** leg is superseded by [ADR-0083](ADR-0083-a-flood-must-not-destroy-finished-work.md) (serial and resumable, so a FloodWait costs a chunk rather than the whole file). The parallel part **upload** and the striped download used by `media download --parallel` remain in force.

## Context

The 2026-07-24 measurement (plan task 8, verbose sync with timestamped
RPC log on a live protected channel) settled the clone-speed question
with numbers: of 43.9s wall time for 5 messages, **56.6% was sequential
chunk upload** (`SaveFilePartRequest` × 86) and **35.7% sequential chunk
download** (`GetFileRequest` × 86) — 92% of the sync is moving file
chunks one RPC at a time. Send requests are 2.7%; roster is noise.

The earlier hypothesis "parallelize across messages" was wrong; the
bottleneck is *inside a single file*. MTProto explicitly supports
fetching file ranges concurrently and uploading big-file parts out of
order — official clients do both; tgcli's own `media download
--parallel` has striped downloads in production without flood incidents.
Peer creation and message sending — the actually flood-prone operations —
are untouched by this change.

## Decision

1. **Chunk transfer inside `_reupload_batch` becomes parallel.**
   - Download: stride-based striping over `iter_download` (the proven
     `media.py` `--parallel` pattern) into the batch tempdir.
   - Upload: N workers issuing `SaveFilePartRequest`/
     `SaveBigFilePartRequest` for distinct parts of the same file.
2. **Parallelism is the constant 4.** No flag, no config key (economy
   principle); files short enough to fit in one part are automatically
   unaffected. Revisit only with a new measurement.
3. **Ordering guarantees unchanged.** Messages and batches are sent
   strictly sequentially; per-batch `record_mapping`/cursor/state.save
   discipline is untouched; only the byte transfer inside one message's
   media is concurrent.
4. **FloodWait during parallel transfer** cancels sibling workers and
   surfaces exactly as today: exit 5, `retry_after`, per-clone cooldown,
   and the ADR-0045 account-scoped cooldown once it ships.
5. **Verify by re-measurement.** After implementation, repeat the
   timestamped `-v` sync protocol on a protected channel and record the
   before/after phase table in DEVLOG. Expected: ~3–4× on media-heavy
   channels (transfer share 92% / parallelism 4 + fixed overhead).

## Consequences

- Protected-channel clone sync drops from ~9s/message (measured) toward
  ~2.5–3s/message on media-heavy sources; text-only and forwarded paths
  are unchanged.
- Up to 4 concurrent requests against Telegram's file DCs during a
  reupload — the transfer endpoints designed for exactly this pattern;
  the flood-prone surfaces gain zero new concurrency.
- A mid-transfer failure aborts the batch before any send, so
  crash-safety semantics (cursor advances only after confirmed send) are
  unchanged.
- `commands/clone.py` grows a worker pool; the media striping pattern is
  reused rather than re-derived (ADR-0043 shared-seam discipline —
  extract a helper if the code would otherwise be copied).
