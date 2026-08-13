# ADR-0103: Clone id includes the source peer class

Date: 2026-08-13
Status: accepted
Form: full (CONTRACT semantics + a persistent-identity change)
Closes: docs/thermos-audit-2026-08-13/tickets/T05-clone-id-kind.md (T05)

## Context

`clone.state.clone_id(account_user_id, source_peer_id)` hashed only the bare
numeric peer id. Telegram's id space is not unique across peer classes: a
`User`, a live basic group (`Chat`), and a `Channel` can all carry the same
integer. Two different sources sharing that integer collided on one clone
state slot — the second `clone init` either refused with a kind mismatch
(exit 2, `clone source kind no longer matches initialized state`) or, with
`--replace`, superseded the *wrong* source's history.

The finer split within `Channel` — broadcast, megagroup, forum — is not part
of the collision: Telegram never reassigns a `channel_id` across channels,
and a megagroup can legitimately toggle into a forum in place (topics
enabled on an existing supergroup) without becoming a different chat. An
existing test
(`test_clone_init_commit_blocks_source_kind_drift_without_state_or_mutation`)
already pins that a stored `megagroup` clone whose live source now resolves
as `forum` must refuse the commit (exit 2), not silently fork into a second
clone slot for the "new" kind.

## Decision

`clone_id` gains a required `source_kind` argument and hashes the source's
**peer class** — `user` (dialog), `chat` (basic group), or `channel`
(broadcast/megagroup/forum) — instead of the raw `source_kind` string. Two
sources with the same numeric id in different classes now land on different
slots; a megagroup and its own later forum toggle still land on the same
slot, where the existing `source_kind`-mismatch check in `commands/clone.py`
keeps refusing that drift exactly as before.

Every call site that used to compute `clone_id(account_user_id,
source_peer_id)` right before a `state.load` now goes through the new
`state.resolve_slot(account_user_id, source_peer_id, source_kind)`, which:

1. Computes the canonical (class-aware) id.
2. Returns it unchanged if that slot already exists, or if no pre-ADR-0103
   (class-blind) slot exists for this `(account, peer_id)` either.
3. Otherwise loads the legacy slot. If it is unreadable (corrupt/ambiguous),
   or its own recorded kind is in a *different* peer class, the legacy slot
   is left untouched — the second case is exactly the collision this ADR
   fixes, so the id asked for must not adopt the other class's data.
4. Otherwise renames the legacy `.db` (plus its WAL/SHM sidecars) onto the
   canonical id, in place, with a one-line stderr note, and returns the
   canonical id.

The migration is a plain filesystem rename triggered lazily by the first
read of an identity after upgrade — no batch/startup migration step, no new
CLI flag. `jobs/runner.py`'s `clone-sync` progress-token lookup gained the
same `source_kind` plumbing (`clone.resolve_source_identity` now returns
`(peer_id, kind)` instead of just the id) so its identity resolution goes
through the same seam.

A pre-existing legacy slot in a genuinely different class (the actual T05
bug) is left at its own id, resolvable once its own class is asked for; it
is visible in `clone status --all` like any other slot the current version
did not create.

## Rejected alternatives

- **Hashing the full `source_kind` string (5-way split).** Breaks the
  megagroup→forum drift-refusal test: a toggled channel would silently get
  a second clone slot instead of the intended commit refusal.
- **A one-time batch migration at startup/`doctor`.** Adds a new surface and
  a new failure mode (partial migration, concurrent access) for a rename
  that is one `os.replace` per identity and idempotent to run twice; lazy
  per-identity migration on the read path that already exists is simpler
  and cannot run more than once per slot.
- **Silently adopting a different-class legacy slot's data.** That is the
  bug, not the fix — the whole point is that two different sources must
  never share history because they share an integer.
- **A new top-level `clone migrate` command.** No command exists to run it
  and no operator action is needed; the migration is transparent and
  correct without a command surface.

## Contract impact

`docs/CONTRACT.md` §11: the paragraph on `clone_id` being deterministic per
source is amended to state it is deterministic per
account/source/peer-class, and documents the one-time, lazy, stderr-noted
migration of a pre-ADR-0103 slot. No flag, JSON field, or exit code changes;
`clone_id` is still an opaque hex string with no format promised beyond
that. `T18` (kind-aware `lookup.matches` filter tokens) is a related,
separate ticket not addressed here.
