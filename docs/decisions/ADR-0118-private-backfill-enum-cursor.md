# ADR-0118: Private archive-backfill enumerates dialogs with a durable cursor

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)
Closes: thermos audit T37

## Context

`--private` archive-backfill job quanta call `enumerate_private_dialogs`,
which restarted `iter_dialogs` from the head on every quantum. On large
accounts the completed head is walked again each time — O(n) dialog RPCs per
quantum — burning pacing, peer-breadth, and wall-clock without progress.

## Decision

1. Persist an internal private-enumeration resume token on
   `account_sync.private_enum_json` (archive schema v8). The token is the
   Telethon `GetDialogs` offset triple: peer class + id/access_hash,
   top-message id, and date.
2. `enumerate_private_dialogs` resumes `iter_dialogs` from that token. It
   advances the token past non-private dialogs and completed private dialogs
   as it walks; pending refs do not advance the token until their backfill
   reports `more=false`.
3. Corrupt or undecodable tokens are cleared and enumeration restarts from
   the head. Exhausting the dialog list with no pending private dialogs
   clears the token so a later job can see newly active dialogs.
4. The token is store-internal only: no CLI flag, JSON field, or CONTRACT
   change. Codec helpers live in `archive/private_enum.py`; persistence goes
   only through `write_account_sync(..., private_enum=|/clear_private_enum=)`.
   Enumerate freezes the durable offset once any incomplete private is
   queued so `max_dialogs` lookahead cannot skip it.

## Rejected alternatives

- Keep restarting from the head and only skip via `sync_state`: still pays
  O(n) RPCs for the completed head every quantum.
- Store only a peer id without date/message offset: Telegram's dialog paging
  needs the full offset triple.
- Put the token in the jobs registry: the archive store already owns
  per-account sync checkpoints and survives job key churn.

## Contract impact

None. Cursor is internal archive state; CLI flags, stdout shapes, and exit
codes are unchanged.
