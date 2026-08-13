# ADR-0103: Public `tg changes` cursors are authenticated per account

Date: 2026-08-13
Status: accepted
Form: full (`docs/CONTRACT.md` semantics and a fail-closed read boundary)
Amends: [ADR-0063](ADR-0063-tg-changes-design.md)
Closes: thermos audit T14

## Context

ADR-0063 made the opaque cursor the sole continuity and subscription record
for `tg changes`, but its `v1:` value is only base64-encoded JSON. Any caller
can therefore forge the common `pts` or a `{channel_id: pts}` entry. A low
channel `pts` replays history as fresh `message_new` events; a high one silently
drops events. Worse, adding a channel directly to the JSON bypasses the
`--peer` path that resolves the selected account's channel and baselines its
current `pts`.

The owner chose the thermos ticket's fail-closed policy for agent-fed cursors:
a channel map must have been established by `--init` / `--peer` for the
selected account, not merely be structurally valid.

Numbering note: `main` ended at ADR-0092 when this decision was written.
Parallel open branches already claimed 0093, 0096, 0097, 0099, and 0100–0102;
the thermos wave reserved that collision-prone range, so this branch uses the
first unclaimed number after it, ADR-0103.

## Decision

1. **The public feed emits authenticated `v2:` cursors.** The payload still
   carries common `pts`/`qts`/`date`/`seq` plus the channel map, but now also
   carries an HMAC-SHA256 over that complete semantic payload. Every public
   result signs its `next_cursor`, including init, ordinary polls, subscription
   changes, waits, and gap rebases.
2. **The MAC key is stable per configured account.** It is derived in memory
   from the configured account alias, API id, and API hash under a fixed
   `tg changes` context. The API hash is the local secret; alias and API id
   provide account separation. Session role is deliberately excluded, so a
   cursor initialized on `--session-role job` remains valid on the same
   configured account's primary or another named role, preserving ADR-0062.
   The key and MAC input are never emitted separately or persisted.
3. **The public `--cursor` seam requires a valid `v2:` MAC before any Telegram
   request.** An unsigned legacy `v1:`, a modified payload, or a cursor from
   another account raises `PolicyError` (exit 2) and tells the caller to run
   `tg changes --init`. This intentionally invalidates old public cursors:
   silently upgrading an untrusted cursor would authenticate forged state.
4. **The archive's local cursor stays `v1:`.** `archive sync` owns its cursor
   inside the account-scoped SQLite store and never accepts it through
   `tg changes --cursor`; it keeps the existing unsigned codec path. Keeping
   this trusted internal format avoids a state migration and does not weaken
   the public boundary.

## Rejected alternatives

- **Accept unsigned cursors only when `channels` is empty.** This preserves
  compatibility but leaves common state forgeable and creates two public trust
  modes. A later `--peer` would also bless attacker-chosen common state while
  adding a safely baselined channel.
- **Bind only an account id in cleartext.** An id proves no provenance; the
  same caller that edits `channels` can edit the id.
- **Use the Telethon session auth key.** It is role-specific and reached through
  private Telethon internals, so cursors would stop crossing named roles and
  the codec would depend on an unstable implementation detail.
- **Persist a random cursor-signing key.** Cryptographically sound, but it adds
  a new persistent state file, backup/lifecycle rules, and failure modes when
  the configured API hash already provides local key material.
- **Document `--cursor` as trusted operator input.** Rejected by the owner's
  explicit fail-closed policy for agent-fed cursors.

## Contract impact

`docs/CONTRACT.md` §12 changes from unsigned `v1:` public cursors to
account-bound `v2:` cursors. `--init` is the migration path. Legacy, modified,
or wrong-account cursors fail with exit 2 before polling; the JSON document,
event vocabulary, plain columns, gap behavior, and no-state-file guarantee are
unchanged. The internal archive cursor is not part of the `tg changes` CLI
surface and remains compatible.
