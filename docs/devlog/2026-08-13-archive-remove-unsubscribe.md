## 2026-08-13 — archive remove drops the changes cursor subscription (Cursor agent)

**Did:** fixed thermos T09 on PR #255: `tg archive remove` left a channel's
pts subscription in `account_sync.changes_cursor` after dropping explicit
scope, so later `archive sync` kept polling `GetChannelDifference` for an
untracked channel. The initial slice added `drop_channel_subscription`; review
then required atomic scope/cursor removal and stale-writer protection under
ADR-0110 (`remove_scope` in one `BEGIN IMMEDIATE` transaction, sync writes
project subscriptions onto current scope). Also shipped thermos T23 in the
same PR: peer-scoped delete events now honor `_in_archive_scope` before
tombstones, with a public `archive sync` CLI regression. Fixed two pre-existing
`ruff` E501s in `scripts/publish-thermos-backlog.py`.

**Decided:** ADR-0110 governs persistent-state safety; no CONTRACT/JSON shape
change — `removed` payload unchanged.

**Learned:** `_ensure_channel_subscriptions` only adds missing scope channels;
remove had no mirror until scope and cursor diverged. Delete apply was the
only peer-scoped path that skipped scope checks.

**Next:** rebase onto `main`, resolve ADR/doc conflicts at integrator merge,
independent review PASS-WITH-NITS on tip `bf43124`.
