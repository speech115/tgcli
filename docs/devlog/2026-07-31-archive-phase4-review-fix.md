# Archive Phase 4 review fixes

## Findings and fixes

- Fixed media candidate selection so the query first reaches rows without a
  published `media_path`; a separate pass repairs rows whose recorded file is
  missing. The queue is no longer bounded by the newest 501 database rows.
- Added a regression with 700 voice messages, 650 already downloaded, and 50
  older unpublished rows. The run downloads the oldest visible backlog item
  and reports `remaining: true` while more work exists.
- When transcription finds a missing media file, the stored path is cleared
  and the row returns to media acquisition instead of remaining in every
  transcription batch. Transcript attempts are not consumed by a missing
  media file.
- Removed a duplicate FTS delete and clarified that backfill uses its fixed
  media budget while `--max-media` is a sync flag.
- Added CLI regressions for negative and over-cap transcription limits.

## Verification

Focused archive tests are green. The full gate passed after these changes:
`1616 passed, 9 skipped`, Pyright 0, coverage 23 namespaces, and docs 0
problems. The previously existing untracked `.codex/` workspace file remains
untouched.
