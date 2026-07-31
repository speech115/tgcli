# ADR-0070: One-shot archive refresh and failure notification

Date: 2026-07-31
Status: accepted (2026-07-31; owner continuation of ADR-0068 Phase 6)

## Context

The archive now has separate bounded commands for delta sync, media
acquisition, and local transcription, but an hourly operator job would still
need to compose them and distinguish a quiet success from a recurring outage.
The repository is deliberately daemonless (ADR-0002), and the archive must
not acquire an unbounded whole-account or whole-history sentinel.

## Decision

Add `tg archive refresh` as a foreground one-shot composition. It runs the
existing bounded archive sync (including its media stage) and then the bounded
offline transcription queue under one per-run `WaitBudget`. The command
accepts the existing sync caps plus explicit transcription caps; an explicit
`--timeout` remains available, while no implicit 60-second deadline clips a
scheduled pass.

Persist an account-level refresh failure streak in `account_sync`. A failed
run-level RPC or local transcription-engine failure increments the streak; an
item-level media/transcription failure is reported in the result but does not
represent an account outage. FLOOD_WAIT is expected rate-limit pressure and
does not increment the streak. Any completed pipeline resets the streak. On
the third consecutive run-level failure, the command makes one best-effort
macOS notification through `desktop.py`. The notification is generic and
contains no message text, account secret, or exception detail. A completed run
starts a new notification episode.

Keep media acquisition retry state separate from transcription attempts:
media failures have a fixed three-attempt cap, with retryable and terminal
`media_status` values. This prevents one unavailable file from remaining in
the queue forever or disabling future outage notifications.

Ship a checked-in launchd plist as a manual template with a 3600-second
interval. The template uses explicit path placeholders and does not install,
load, or supervise a resident process. Operators may substitute paths and run
`launchctl` themselves.

## Rejected alternatives

- A resident daemon or automatic launchd installation would violate the
  daemonless CLI boundary and add an owner-unapproved lifecycle.
- A single unlimited `refresh` pass would recreate the historical cap/cursor
  data-loss class; each acquisition stage keeps its hard ceiling.
- Notification text containing the last exception or message content would
  leak untrusted archive data into the desktop surface.
- Per-dialog counters alone cannot describe one failed account-wide run, so
  the consecutive-run counter belongs in `account_sync`; per-dialog
  freshness/errors remain in `sync_state`.

## Contract impact

`tg archive refresh` and its bounded flags are added to CONTRACT.md. Archive
stores migrate from schema v5 to v6 by adding independent media retry fields
to `transcripts`; existing message, revision, tombstone, media, and transcript
data is preserved. The media retry seam lives in `archive/media.py` so the
SQLite store remains focused on persistence and FTS.
