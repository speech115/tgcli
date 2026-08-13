# ADR-0112: Split clone send execution out of the command module

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)
Owner request: thermos debt T28

## Context

Thermos audit ticket T28. `commands/clone.py` sat at the architecture
ceiling (1296 lines). RPC send orchestration for a synced batch —
`_forward_batch` and `_reupload_batch` — lived in the command module
while `clone/reupload.py` only owned bytes on disk. The command surface
should read as sync glue; transfer and send execution belong in clone-owned
helpers.

## Decision

Move `_forward_batch` and `_reupload_batch` (plus their private helpers)
into `tgcli.clone.send`. The command module imports `clone_send.forward_batch`
and keeps the existing `sync_text` → `copy_batch` → `quotes.send_with_degrade`
call chain unchanged at the CLI boundary.

Ratchet `commands/clone.py` down and add a ceiling for `clone/send.py`.

## Rejected alternatives

- **Extend `reupload.py` with send orchestration.** Reupload owns download
  cache and upload bytes; forward, snapshot, reforward, and mapping save
  are a wider seam than media transfer.
- **Leave send logic in `commands/clone.py` and raise the ceiling.** Would
  not address the debt; the command module would keep growing with every
  send-mode branch.

## Contract impact

None. No CLI flag, JSON shape, exit code, audit record, or Telegram request
construction changes.
