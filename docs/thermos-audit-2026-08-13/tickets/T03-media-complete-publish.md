---
id: T03
priority: P0
labels: [bug, ready-for-agent]
slice: Media & transfer
title: "Media download: never publish incomplete / unsynced bytes"
---

## Problem

`tg media download` can publish a final path and exit 0 when the stream
ended early (serial) or when a striped/parallel transfer left a sparse
file (parallel). Serial checkpoints `flush()` without `fsync`, unlike
`transfer.download_resumable` / clone (ADR-0083).

## Expected

Mirror ADR-0083 clone guards:

1. Before `_publish`, require `current == size` when size is known.
2. Checkpoint only after flush+fsync (or call `download_resumable`).
3. After `download_striped`, verify completeness before publish.

## Evidence

- `src/tgcli/commands/media.py` (serial loop + parallel publish)
- `src/tgcli/transfer.py` (`_record`, `download_resumable`, `download_striped`)
- `src/tgcli/clone/reupload.py` (short-stream refusal)
- Thermos Wave 6 security P1#1–3; quality recommends routing through transfer

## Acceptance

1. Tests: short stream never publishes final name (serial + parallel).
2. Test: part file is fsynced before checkpoint claims bytes.
3. Full gate green.

## Note

T31 (route through `download_resumable`) may absorb this; if T31 lands
first, keep these regression tests.
