## 2026-08-09 — clone sync answers for its own legs

**Did:** landed ADR-0082 (#171, #174). `clone sync`/`clone refresh` now
resolve a bare title through recorded clone state (new `clone/lookup.py`:
id/title matcher, slot scan, recorded-source peer ref) instead of handing it
to Telethon's entity cache; a source this account cannot open is exit 4
`clone source not found`, not an exit-1 `CHANNEL_PRIVATE`. `SyncProgress.phase`
takes the leg's own `copied` and `copy_batch` resolves `~total` against the
leg's own entity, so the comments phase counts comments against the discussion
group. Each interleaved window announces `posts` before it runs.

**Decided:** a title stays acceptable everywhere rather than being refused in
`sync` alone — `status`, `export-state`, and `refresh` all take one. The
`roster` line keeps counters and shows `0/~?`; a second line format for a
phase that copies no messages is not worth it.

**Learned:** `telethon.utils.parse_username` returns `(None, False)` for a
title and `(name, False)` / `(code, True)` for usernames and invite links —
that is the whole test for "Telegram can resolve this". The `998/~997` line
came from two independent bugs stacked: `copy_batch` always re-resolved the
total against `source_entity`, and `phase()` reset only the total.

**Next:** #169/#170 — a flood must not destroy finished work.

**Review fixes (2026-08-10):** the widened peer-refusal catch also changed
`clone init`'s exit code from 1 to 4 without being documented or tested —
CONTRACT now states the rule for all three clone commands and `init`/`refresh`
have their own regression tests. `resolve_total` memoizes per entity instead
of per phase, so the ADR-0051 interleave no longer spends a GetHistory-family
request per window on a number it already has. The same `except ValueError`
shape in seven non-clone commands is flagged as issue #179, not ported here.
