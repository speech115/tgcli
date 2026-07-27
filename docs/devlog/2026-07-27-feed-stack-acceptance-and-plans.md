# 2026-07-27 — ADR-0060/0062/0063 accepted; three execution plans (Claude Fable 5)

**Did:** owner review session (structured grill) over the three proposed
ADRs; all three accepted with amendments recorded in the ADRs themselves.
Wrote the execution plans:
`docs/superpowers/plans/2026-07-27-clone-state-sqlite.md`,
`…-session-roles.md`, `…-tg-changes.md`. Updated the ADR index rows and
the FEED-001 entry in ISSUES.md (blocker resolved by ADR-0062
acceptance). Docs only; no code.

**Decided:**

- Three sequential releases — SQLite → session roles → `tg changes` —
  each its own plan/branch/release; working branches are created at
  phase start, never in advance.
- ADR-0060: single reader (no transitional dual-format code); rollback
  via an explicit `clone export-state` command; `.imported` backups
  never auto-deleted.
- ADR-0062: role names are arbitrary (`primary` reserved) because one
  role is one lock lane and "clone runs while changes polls" already
  needs two; `--session-role` is a global flag; no implicit fallback
  stays as the attribution guarantee.
- ADR-0063: hybrid coverage — Telegram's common updates state does not
  cover channels (per-channel `pts`, `getChannelDifference`), so
  subscribed channels (held **in the cursor**, changed only via explicit
  `--peer`/`--drop-peer`) get full events and everything else gets
  `channel_activity` signals. Full `read`-shape bodies (one message
  form in the contract), fixed 2 s settle window inside the `--wait`
  deadline, `read_marker` dropped from v1 (visible in `skipped` until
  demand exists), gaps carry a scope (`common` vs one channel).
- Live acceptance is the release gate for all three: merge on green
  gate is allowed, version bump + tag only after the acceptance slice
  passes with the owner present (test account first).

**Learned:** the channel-coverage hole was invisible in the ADR-0063
draft because `getDifference` reads as "the" difference API; writing the
subscription contract exposed that the cursor must carry per-channel
state and that gap objects need a scope. Cursor-held subscriptions won
over per-call `--peer` flags for the same reason the rest of the design
is loud: a forgotten flag must not silently drop a channel.

**Next:** Cursor executes the SQLite plan first (branch from `main`);
integrator merges, bumps, tags after live acceptance; then roles, then
changes. FEED-001 closes with the third release.
