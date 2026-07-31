# Archive Phase 4: media and transcription

## What changed

- Added schema v4 transcript queue fields for controlled media paths, media
  kind, retry state, and the last error.
- Added bounded, idempotent voice/video-note acquisition to archive backfill
  and changes sync. Media publication is completed before the queue row is
  marked available; the sync difference is still applied in full before its
  cursor advances.
- Added the offline `tg archive transcribe` command. It drains the newest
  ready rows through the local FluidAudio/Parakeet `transcribe` CLI, stores
  transcript text plus engine/model metadata, refreshes FTS, and distinguishes
  retryable failures from terminal or exhausted `no_transcript` rows. Status
  summaries expose the queue/errors, and the terminal marker is searchable.
- Kept existing transcript text when an archived message is edited or its FTS
  row is rebuilt.
- Updated the CLI contract, archive guide, feature matrix, map, skill route,
  proposal status, and this per-session devlog.

## Acceptance

The local Russian voice-note acceptance used the installed Parakeet v3
runtime and completed successfully: `ru`, 1 speaker, 09:58 duration, 1,394
words, 23 turns, and approximately 15x ASR. The acceptance artifact stayed
outside the repository.

Focused archive, CLI, read-shape, grammar, lifecycle, docs, and architecture
tests are green. No Telegram media live smoke was run in this session; the
download boundary is covered by an idempotency test and the external media
path remains owner/session-gated.

## Decisions and follow-up

- No subagents were used; the implementation stayed in this session.
- `--max-media` caps expensive media downloads only. It never truncates
  already-received difference events before checkpointing.
- Phase 5 remains responsible for richer search filters, ranking, paging, and
  archive lifecycle commands such as refresh and purge.
