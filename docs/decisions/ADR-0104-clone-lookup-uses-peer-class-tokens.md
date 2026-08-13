# ADR-0104: Clone lookup uses peer-class tokens

Date: 2026-08-13
Status: accepted
Form: full (CONTRACT semantics)
Closes: docs/thermos-audit-2026-08-13/tickets/T18-clone-lookup-kind.md (T18)
Depends on: ADR-0103

## Context

ADR-0103 lets a user, basic group, and channel with the same bare numeric id
own distinct clone slots. `clone.lookup.matches` still compared every numeric
filter with both the bare id and a channel-marked `-100…` id, regardless of
the stored source kind. A dialog or basic-group clone could therefore match a
channel token, a basic group's canonical `-N` token did not match at all, and
the distinct slots introduced by ADR-0103 could become ambiguous again in
`clone status` and `clone export-state`.

## Decision

Numeric clone filters match exactly the canonical Telethon peer id implied by
the clone's recorded source kind:

- `dialog` uses `PeerUser`, whose token is the raw positive id;
- `basic` uses `PeerChat`, whose token is the negative `-N` id;
- `broadcast`, `megagroup`, and `forum` use `PeerChannel`, whose token is the
  channel-marked `-100…` id.

Tokens belonging to another peer class do not match. Title substring matching,
the guarded integer parse, unreadable-slot handling, and unknown/ambiguous exit
behavior are unchanged. `status`, `export-state`, and recorded title lookup
continue sharing the one `clone.lookup.matches` seam.

The implementation reuses the existing source-kind-to-peer constructor map and
Telethon's `get_peer_id`; it does not duplicate the marking arithmetic or
change persistent state.

## Rejected alternatives

- **Keep accepting a channel's bare id as an alias.** A bare id can identify a
  user with the same number after ADR-0103, so the alias recreates ambiguity.
- **Accept every peer-class token and disambiguate later.** `status` is a
  filter and may return several rows, while `export-state` requires exactly
  one; late command-specific rules would make the shared matcher disagree
  again.
- **Compute `-N` and `-100…` manually.** Telethon already owns the canonical
  peer-id encoding, and using it keeps lookup aligned with entity resolution.

## Contract impact

`docs/CONTRACT.md` §11 now defines numeric clone filters by source peer class:
raw positive for dialogs, negative for basic groups, and `-100`-marked for
channels. The previously documented bare-id alias for channels is removed.
No flag, JSON shape, persistent schema, or exit code changes.
