## 2026-07-27 — Live acceptance: clone state SQLite (Cursor Grok)

**Did:** live-accepted ADR-0060 on test account `vermassov` from
`cursor/clone-state-sqlite-cc3b` via `uv run tg` in a worktree.

Also removed the leftover session role from the earlier roles acceptance:
`accounts remove vermassov --role job --confirm` → local
`vermassov@job.session` deleted (Telegram Devices may still list the
authorization until the owner terminates it in Settings → Devices;
`remove --role` does not call LogOut).

JSON → SQLite import (production `TGCLI_STATE_DIR`):
- first `clone status` imported readable v2 JSON clones → `.db` +
  `.json.imported` (byte-preserved); `schema_version: 1`,
  `integrity: "ok"` for vermassov's clones (Пылесос,
  Архитеркутра Лидерства) and other accounts' clones in the shared dir
- older/incomplete JSON left as `integrity: "json-pending-import"` /
  `unreadable: true` (expected)
- `clone refresh -1003740847993` on an imported vermassov clone returned
  a preview (eligible empty) without crash
- `clone export-state "tgcli comments demo"` printed v2 JSON;
  `CloneState.from_dict` round-trip OK

Fresh disposable clone (source `-1003990078766` Пылесос, 45 posts,
`--no-comments`):
- `clone init` → commit created destination `-1004448204547`
  `[Clone] Пылесос`; state file is `.db` only (no `.json`)
- mid-sync SIGTERM after ~28 copied posts left one orphan destination
  message past `max_destination_id` (Telegram RPC succeeded, `state.save`
  not yet) → resume blocked with `unexpected tail messages`
- deleted orphan dest id 32; resume completed: cursor 50, id_map 41
  unique source/dest, dest `count` 45, no duplicate mappings; files
  `.db` + WAL/SHM + participants jsonl only

**Decided:** the Ctrl-C window between a successful forward and
`state.save` is the same crash class as under JSON; SQLite dirty/WAL did
not invent it. Acceptance treats orphan-delete + resume as the repair
path, not a release blocker.

**Learned:** `clone export-state` / `init --commit` take SOURCE (not
clone_id); shared `clones/` mixes account ids — filter by
`account_user_id` when reading live status.

**Next:** independent Spec+Standards review of #92; integrator merges
after OK, then #93 / #94; version bump + tag only after all three
acceptance slices.
