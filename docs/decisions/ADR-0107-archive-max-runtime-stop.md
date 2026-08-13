# ADR-0107: Archive sync/backfill honor --max-runtime through media

Date: 2026-08-13
Status: accepted (owner request: thermos T10 / all-37 campaign)
Form: ADR-lite (released command stop contract)

## Context

Jobs already passed `should_stop` into archive sync. CLI `archive sync` /
`backfill` ignored `--max-runtime` for media tails: backfill could stop
between dialogs then still run uncapped `fetch_media`. That defeats the
wall-clock stop contract agents rely on.

## Decision

Thread `pacing.wall_clock_remaining` / `should_stop` through CLI archive
backfill and sync (via dispatch) and every `fetch_media` call site,
including job quanta. When the clock is exhausted, set
`stop_reason: wall_clock_cap` and do not start further media downloads.

## Rejected alternatives

- Document-only caveat — still burns wall time on media after the cap.
- Cap only sync, not backfill — leaves the reported uncapped tail.

## Contract impact

`archive sync` / `backfill` under `--max-runtime` may report
`stop_reason: wall_clock_cap` and leave media remaining when the cap
hits during or before media fetch. Patch release on merge.
