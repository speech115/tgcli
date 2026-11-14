# ADR-0126: Clones stay current by a scheduled `clone sync`

Date: 2026-11-14
Status: accepted
Amends: ADR-0125 decision 3 (long floods are the caller's)

## Context

ADR-0125 left a long FloodWait to the caller and named `examples/clone-watch`
as the caller: a `tg run` listener launchd kept alive, which polled for new
messages and ran `clone sync` after each burst. The invocation journal showed
what a plain schedule cost instead. An idle `clone sync`, with nothing new in
the source, took a median 20 requests and about 6 s: it resolves both
destination peers, walks the destination tail, reads the source history and
lists the roster. A launchd job every five minutes spends about 5,800 requests
a day to learn that nothing happened. And with the governor gone, a scheduled
run that follows a long FloodWait sends its first request into the live ban.

## Decision

1. **An idle sync ends early.** `clone sync` reads the newest message of the
   source, and of the source discussion group when comments are copied, with one
   `messages.GetHistory` each (`limit=1`) on peers the session already holds.
   If neither is past `cursor` and `discussion_cursor`, it exits 0 with
   `sync.up_to_date: true`. Telegram numbers a chat's messages in one rising
   sequence, so nothing at or below the cursor can be new. The work a full run
   does that no new message triggers is named in `clone/idle.py`; the roster is
   the one that needed its own trigger.
2. **A long flood is remembered in the clone.** A `clone sync` that ends in a
   FloodWait writes `now + retry_after` to `CloneState.retry_not_before`, the
   field ADR-0045 added and ADR-0125 left unused. The next `clone sync` of that
   clone exits 5 with the remaining `retry_after` before any request about the
   source or destination, and a completed run clears it. The memory is per
   clone, not per account: another clone's sync, or any other command, still
   sends and may draw its own wait.
3. **The roster is listed at most once a day.** The age of the participants
   sidecar, by mtime, decides; no state records it. A day-old roster also keeps
   a run from being idle, so the destination checks and the pin read happen at
   least daily as well. A deferred listing leaves the sidecar alone, so its
   retry is the next run.
4. **`examples/clone-watch` is removed.** One launchd job per clone, with
   `StartInterval` 120 and `--max-runtime 240`, replaces the listener
   (docs/OPERATIONS.md, Keeping clones current). Freshness is bounded by the
   interval, not by a push, and the listener's macOS notification on repeated
   failure is gone; the invocation journal records every run's exit.

## Consequences

- An idle scheduled run costs the session check and one or two history reads
  (the journal's 20 requests and 6 s become a few requests); a clone with
  nothing new for a day does one full run a day.
- A change that moves no message id past the cursor is seen only by that daily
  run: a pin placed without a service message, a destination renamed by the
  owner, a destination deleted or tampered with.
- A run under a long flood sends nothing but the session check, so a two-minute
  schedule cannot extend the ban.
- A flood on a send that Telethon could already ride out (30 s or less) is
  still slept and sent again by `Pace.call`; only the one that ends the run is
  remembered.
- `retry_not_before` is reported by `clone status`.
