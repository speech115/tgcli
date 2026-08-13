## 2026-08-13 — archive delete events respect scope (Cursor agent)

**Did:** fixed thermos T23 on the existing T09 branch. Added a regression
test that leaves a channel message and sync metadata in the archive after
the channel has left explicit scope, then delivers a peer-scoped
`message_delete`. The red test showed one incorrect tombstone. Updated
`archive.sync.apply_events` to route peer-scoped deletes through the existing
`_in_archive_scope` guard before inserting tombstones. Peer-less private
deletes retain their existing local-message lookup behavior.

**Decided:** the apply guard restores ADR-0068's explicit channel scope
boundary and adds no output shape. ADR-0110 was added later for the coupled
T09 transaction/concurrency safety correction; CONTRACT remains unchanged.

**Learned:** delete events were the only peer-scoped apply path that built its
targets without consulting archive scope, allowing stale subscribed channels
to mutate local tombstones after removal.

**Next:** independently review the combined T09/T23 diff before merge.
