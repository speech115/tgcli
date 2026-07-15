# Protected mirror reupload transport (Stage 1 consolidation)

## Goal
`tg mirror sync` copies protected (noforwards) sources by reconstruction:
download media, re-send content — instead of refusing. Lab branch
(`claude/mirror-r1-controlled-lab`) proved the transport live on 2026-07-13
(protected reconstruction 9/9); this slice ports it into the product sync
with the existing store invariants intact.

## Design
- Transport decision per batch: `reupload` when the source channel has
  `noforwards` or any message in the batch does; otherwise the existing
  native `ForwardMessagesRequest` path. No CLI flag changes.
- Reupload dispatch reuses the same journal: `prepare_batch` random ids go
  into raw `SendMessageRequest` / `SendMediaRequest` / `SendMultiMediaRequest`
  calls, confirmations are matched with the existing strict
  `UpdateMessageID` correlation (plus `UpdateShortSentMessage` for bare
  text), retries replay the same random ids.
- Content mapping (same allowlist as native — anything else still stops):
  - no media → `SendMessageRequest` (`no_webpage=True`, entities preserved)
  - webpage → `SendMessageRequest` with preview regeneration
  - photo → download → `upload_file` → `InputMediaUploadedPhoto`
  - document → download → `upload_file` → `InputMediaUploadedDocument`
    with source `mime_type` + `attributes` (lab-proven fidelity)
  - album → per-item `UploadMediaRequest`, then one `SendMultiMediaRequest`
    with per-item random ids and captions
- Downloads land in a `tempfile.TemporaryDirectory` per batch and are
  removed afterwards; a failed download stops the sync with a clear error.
- FloodWait anywhere in the reupload batch records the account cooldown
  exactly like the native path. Audit action: `mirror-sync-reupload`.

## Tests
Replace the three "refuses protected" tests with reupload behavior tests;
add coverage for text/photo/document/album mapping, attribute preservation,
random-id replay on restart, temp-file cleanup, download failure,
FloodWait cooldown, and mixed open-channel/protected-message syncs.

## Out of scope
Polls/geo/contacts/etc. (still stop with "not supported"), comments,
non-channel topologies (Stage 2), any lab manifest/verdict machinery.
