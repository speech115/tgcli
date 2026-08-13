## 2026-08-13 — archive remove drops the changes cursor subscription (Cursor agent)

**Did:** fixed thermos T09: `tg archive remove` left a channel's pts
subscription in the persisted `account_sync.changes_cursor` after dropping it
from explicit scope, so a later `archive sync` kept paying for
`GetChannelDifference` on a channel the account no longer tracks. Added
`archive/sync.drop_channel_subscription`, wired into
`commands/archive.remove_chat`, plus a red→green boundary test asserting
`GetChannelDifferenceRequest` is never sent for a dropped channel. Also fixed
two pre-existing `ruff` E501s in `scripts/publish-thermos-backlog.py` that
blocked the gate.

**Decided:** no ADR — this restores stated intent.
`changes_cursor.without_channel` already existed for exactly this purpose
(used by `tg changes --drop-peer`) but was never called from the archive
surface; wiring it in is the minimal fix, not a new feature. No CONTRACT/JSON
shape change — `removed` payload is unchanged.

**Learned:** `_ensure_channel_subscriptions` only *adds* missing scope
channels to the cursor on each sync; nothing mirrored it on the remove path,
so scope and cursor state silently diverged. Same shape as T23
(delete-scope) — left unshipped this slice; the ticket suggested pairing them
but the task scope was T09 only.

**Next:** T23 (`archive/delete` scope gate for tombstones) remains open per
its own ticket.
