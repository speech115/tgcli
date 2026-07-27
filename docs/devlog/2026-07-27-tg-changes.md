## 2026-07-27 — `tg changes` daemonless feed (ADR-0063) (Cursor Grok)

**Did:** implemented FEED-001 / ADR-0063 on `cursor/tg-changes-cc3b`
(stacked on session-roles): `changes_cursor.py` (v1 opaque codec +
Hypothesis round-trip), `commands/changes.py` (GetState / GetDifference /
GetChannelDifference, hybrid channel coverage, gaps, `--wait` + 2 s
settle), CLI wiring, CONTRACT §12, FEATURES `updates` row, guide, SKILL,
ISSUES. Live acceptance deferred to owner.

**Decided:** channel baseline via `channels.getFullChannel.pts` (pinned in
boundary test); private `UpdateDeleteMessages` counted in `skipped` (no
peer in the TL update).

**Learned:** `--wait` must clear the implicit 60 s `--timeout` default or
the wait budget is clipped.

**Next:** owner live-accepts session-roles (#93) and this PR, then
integrator tags the three releases.
