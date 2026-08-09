## 2026-08-09 — diagnostics report what an operator can act on

**Did:** landed ADR-0081 (#172, #173, #175). `doctor` now chmods loose
preview files back to 0600 before it checks them and reports
`preview_perms_repaired`; `--readonly` skips the repair and keeps the old
red. `clone status` counts unimportable `.json` slots as `pending_import`
instead of listing ten all-null rows, hides them behind `--all`, and prints
a stderr pointer when it hid any. `CloneState` gained
`destination_title`/`destination_username` (recorded by `clone init --commit`
and `clone sync`), `clone status` emits a `destination` object in place of
`destination_id`, and `statedb` grew a forward-only migration table
(SCHEMA_VERSION 2).

**Decided:** a diagnostic that already has to `stat` a file may fix its mode;
state slots this version cannot import are counted, never deleted or
auto-imported; `status` stays offline, so the destination name comes from
state, not a resolve.

**Learned:** previews have been written 0600 since 1.1.2 (`7f0e304`) — the
0644 files on the reporting machine all predate it, so #172 was never a write
bug. The ten `json-pending-import` slots are `version: 1` documents from
mid-July; `probe_paths` labels *any* unloadable `.json` that way, which is
why the count is worded "cannot be imported" rather than "legacy".

**Next:** #171/#174 (sync source resolution and phase counters), then
#169/#170 (a flood must not destroy finished work).
