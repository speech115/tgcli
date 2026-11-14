# ADR-0125: Remove the request governor

Date: 2026-11-14
Status: accepted
Supersedes: ADR-0072, ADR-0089, ADR-0090, ADR-0117
Amends: ADR-0122 — the 3 s send interval and its shared slot stay, held by
the loop that sends; its per-type cooldown goes with the ledger

## Context

ADR-0072 put one seam around Telethon's `_call`: a registry of request
classes, per-process pacing, a SQLite ledger of flood cooldowns shared by
every process, a slow start after each cooldown, and a `--timeout` that did
not count the governor's own sleep. Four later ADRs patched its edges. By
November 2026 it was about 840 lines of source and 2,350 lines of tests of
its internals, and every command paid for it: a ledger open per session, a
wrapped `_call`, and journal fields that only the governor could explain.

The invocation journal from 2026-07-11 to 2026-11-14 shows what it did after
it shipped on 2026-08-02:

- No FloodWait exceeded 46 minutes. Of 179 flood stops, 128 were 60 s or
  shorter. Every long one was a write: clone sends, `EditPhoto`,
  `ImportChatInvite`.
- The cross-process ledger refused 29 times in four months.
- Pacing slept for real only in loops: `clone init|sync` about 14 h, `tg run`
  3.7 h, bulk media 3.3 h, export 2.2 h, archive 1.3 h.
- Without `--max-runtime` the governor never slept a flood, and it had set
  Telethon's own sleeper to zero. A 3 s FloodWait failed an interactive
  command with exit 5.

## Decision

Delete `src/tgcli/governor/` and `resolve_phone.py`. Three smaller pieces
take over its jobs.

1. **Short floods: Telethon.** Every client is built with
   `flood_sleep_threshold=30`. Telethon sleeps a FloodWait of up to 30 s and
   sends the request again; a longer one raises, and the command exits 5
   with `retry_after`, as before. A mutation-safe session (`clone init
   --commit`, `clone sync`, `tg run --write`) keeps `request_retries=0`, and
   Telethon has no setting that resends after a flood only: it would sleep
   the flood and raise it anyway. Those sessions set the threshold to 0, so
   a flood raises at once; the resend below covers the short ones.
2. **Loops: explicit pacing.** `tgcli.pace.Pace(interval, free=5)` is
   start-to-start spacing that one loop holds and awaits before each
   request. Every `iter_messages` call in a loop or bulk walk passes
   `wait_time=3`, so Telethon waits before each history page after the
   first; the architecture check fails a call with no `wait_time`.
   `pace.paged` does the same for dialog and participant listings, which
   take no `wait_time`. A command that reads
   one chat at the owner's request (`read`, `search`, `latest`, `media
   manifest`) passes `wait_time=pace.INTERACTIVE_PAGE_WAIT`, which is `None`,
   Telethon's own default: no wait up to 3000 messages, 1 s a page beyond.
   The check accepts either constant.
3. **Long floods: the caller.** tgcli stores no flood. After exit 5 the
   caller waits `retry_after` seconds before the next run.
   `examples/clone-watch` does.

A mutation-safe session cannot lean on Telethon for a short flood, so the
call site that knows a send is safe to repeat resends it:
`pace.Pace.call(send)` waits for its slot, sends, and on a FloodWait of up
to 30 s that fits what `--max-runtime` has left sleeps it and sends once
more. A FloodWait means Telegram did not run the request, so the resend
cannot duplicate it. The sends of `clone sync` and the writes of `tg run
--write` go through it; a second flood, or a longer one, exits 5 with its
own `retry_after`. A timeout or a 500 is never resent: its outcome is
unknown.

`--max-runtime` (ADR-0107) is unchanged and lives in `tgcli.pace`.
`--timeout` counts wall time.

What replaced each governor job:

| Governor job | Now |
|---|---|
| history and search reads, 3 s after five requests | `wait_time=3` on every `iter_messages` in a loop or bulk walk, Telethon's default for a read of one chat; a `Pace` between the dialogs of `archive backfill` and the channels and catch-ups of `archive sync` |
| dialog and participant enumeration, 3 s after five | `pace.paged` in `archive backfill --private`, `clone init`, the `clone sync` roster and `export subscribers`; a `Pace` between the prefix queries of a broadcast export |
| media, 3 s per file after five | a `Pace` per file in bulk `media download`, the `archive sync` media pass and clone reuploads |
| sends, 3 s in one shared slot (ADR-0122) | one `Pace` per `clone sync` run, awaited before each `sendMessage`, `sendMedia`, `sendMultiMedia` and `forwardMessages` |
| `tg run` scripts, paced like any command | the write gate sends each write through `Pace.call`; reads in a script are not spaced |
| `contacts.resolvePhone`, 3 s across processes | nothing: no command resolves more than one phone per run |
| cooldown ledger, local refusal across processes | nothing; the caller honors `retry_after` |
| slow start for an hour after a cooldown | nothing |
| sleep a flood that fits `--max-runtime`, resend once | Telethon's 30 s threshold on read sessions; `Pace.call` for clone sends and `tg run` writes |
| `--timeout` does not count governed sleep | plain wall time; bulk commands still have no default deadline |
| `doctor`: `governor_cooldowns`, `governor_degraded*` | removed |
| `clone status`: `cooldown_until` | removed |
| journal: `governed_sleep_ms`, `request_count`, `floods`, `flood_wait_s`, `request_type`, `provenance` | removed; `retry_after` stays on a flood exit |

## Consequences

- A read that meets a short flood now succeeds late instead of exiting 5.
- Two runs started back to back are no longer held apart. A scheduler that
  reruns on exit 5 without waiting `retry_after` sends into the live
  penalty; under the governor that rerun sent nothing.
- An interactive read is as fast as before: `read --limit 300` waits on no
  page. Only a read inside a loop or a bulk walk waits 3 s a page.
- A command waiting on a busy session now times out at its `--timeout`
  (`TIMEOUT`, exit 1) when that comes before the 120 s busy wait ends.
- `export messages` passes `wait_time=0`: it reads inside a takeout session,
  which lifts the history limits, and the governor never paced it either
  (it saw only the takeout wrapper type).
- A `tg run` script that reads many chats in a loop spaces them itself.
  The 2026-07-31 flood, 791 dialogs read with no gap, is prevented in
  tgcli's own loops and no longer in scripts.
- Telethon builds a CDN download client itself, with its default 60 s
  threshold. tgcli no longer reaches into that factory.
- `governor.db` and `resolve-phone.cooldown` under the state directory are
  dead files; nothing reads or removes them.
