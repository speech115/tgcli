# ADR-0082: `clone sync` resolves and counts per leg

Date: 2026-08-09
Status: accepted
Form: ADR-lite (ADR-0058)
Closes: #171, #174

## Context

Two reports from one live `clone sync` run, both cases of the command
answering about the wrong thing.

`tg clone sync "Насрал в настав"` — the source passed as a bare title —
failed with `CHANNEL_PRIVATE`: *"the channel specified is private and you lack
permission to access it. Another reason may be that you were banned from it"*,
exit 1. The same sync with `@sral_v_nastav` worked. Telegram does not resolve
titles; Telethon falls back to its session entity cache, finds the peer by
name, and issues `channels.GetChannels` with a cached access hash the server
rejects. The operator is told they are banned from a public channel they read
fine, and `clone status`/`export-state` accept titles, so the inconsistency
reads as a bug in the account, not in the argument.

The same run printed `[sync 4301599563] 998/~?` and then `998/~997` during the
comments phase. `SyncProgress.phase()` cleared the total but kept the copied
count, and `copy_batch` always re-resolved the total against `source_entity` —
the channel — even while the comments leg was copying the discussion group.
So the numerator counted every mapped message of both legs and the
denominator described one of them.

## Decision

1. **A title that names a clone resolves from state.** `clone sync` and
   `clone refresh` pass the account id to `_resolve_source`; when `SOURCE` is
   neither an id nor a username/invite link (`telethon.utils.parse_username`
   is the test), the recorded clone states for that account are searched with
   the same id/title matcher `clone status` uses, and the single match's
   recorded `source_peer_id` becomes the peer reference. Two matches is exit 2
   naming both; no match falls through to Telegram unchanged. `clone init`
   keeps the plain path — an uninitialized source has no state to search.

2. **A source this account cannot open is `NOT_FOUND`.** `_resolve_source`
   catches the whole peer-refusal family (`ValueError`,
   `ChannelPrivateError`, `ChannelInvalidError`, `ChatForbiddenError`), not
   just `ValueError`, and raises `clone source not found` — exit 4 instead of
   a raw exit-1 RPC message.

3. **Progress counters are per leg.** `phase(name, copied=…)` restarts both
   the copied count and the total, `copy_batch` resolves the total against the
   leg's own source entity, and each interleaved window announces `posts`
   before it runs. Copied can no longer exceed the total, and the comments
   phase counts comments against the discussion group's size.

4. **Clone lookup is its own module.** `clone/lookup.py` owns the id/title
   matcher, the slot scan, and the recorded-source reference;
   `commands/clone.py` keeps the decisions.

## Rejected alternatives

- **Failing fast on a title instead of resolving it.** The issue allows it,
  but `clone status`, `clone export-state`, and `clone refresh` all accept
  titles; making `sync` the one that refuses trades a confusing error for an
  inconsistent one.
- **Validating `entity.id == clone_state.source_peer_id` after `get_entity`.**
  It catches the wrong-peer case but still spends the failing RPC and still
  cannot resolve the title the operator actually meant.
- **Keeping one running counter for the whole sync.** That is what printed
  `998/~997`; no total exists that describes both legs.
- **Dropping the counters from the `roster` line.** It would need a second
  line format for a phase that copies no messages; `0/~?` is true.

## Contract impact

`docs/CONTRACT.md` §11: `clone sync`/`clone refresh` accept a title that names
exactly one clone of the active account; an ambiguous title is exit 2, and an
unreachable source is exit 4 (`clone source not found`) rather than exit 1.
Progress lines are stderr and not contract data (ADR-0049), so their new
per-leg counters change no documented shape.
