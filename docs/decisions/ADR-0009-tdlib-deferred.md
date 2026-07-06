# ADR-0009: TDLib deferred — no backend in v1; evidence-gated re-entry

Status: accepted (2026-07-06). Supersedes ADR-0006.

## Context

ADR-0006 planned a TDLib fallback backend for phase 3, citing the old stack:
"some media downloads reliably via TDLib where Telethon paths failed".
A re-audit of the old stack's own records shows that claim was inherited,
not proven:

- The old stack's ADR (`control-plane/docs/adr/2026-06-21-tdlib-is-not-default-runtime.md`)
  already ruled TDLib out as a runtime and required a *measured* PoC gate.
- That PoC (`experiments/tdlib-media-poc`) was scaffolded but **never produced
  results** — no `data/RESULTS.md` exists. There is no benchmark showing TDLib
  beats Telethon at anything.
- The 2026-07-06 private-channel incident that "proved" TDLib had these actual
  root causes (`mcp/.planning/2026-07-06-tdlib-download-reliability.md`):
  1. the only member account (`vermassov`) had a **revoked** Telethon session —
     operational, not a library flaw;
  2. Telethon `get_entity(-100<id>)` needs a warm entity cache for `t.me/c/`
     links, and an `iter_dialogs` workaround hit a Telethon 1.44 parse bug
     (`TypeNotFoundError`, constructor `0000000b`) on that channel;
  3. the prod TDLib backend was not installed and was hardcoded main-only —
     the successful TDLib download was a one-off manual script.

What TDLib actually is: a full client engine (own encrypted database, file
store, update loop, own authorization). Telethon sessions cannot be converted;
every account would need a second login and a second state tree. For a
stateless CLI that is a heavy, state-duplicating dependency.

Genuine TDLib capabilities we would gain, for the record: secret chats
(already excluded in FEATURES.md), `getMessageLinkInfo` (resolves private
links without an entity cache), built-in resumable downloads. The latter two
are implementable in Telethon directly.

## Decision

- **No TDLib backend in the v1 plan.** `src/tgcli/backends/tdlib.py` is
  removed from MAP; phase 3 is Telethon-only.
- Phase 3 must instead fix the real root causes in our own code:
  resolve `t.me/c/` links without assuming a warm cache (dialogs scan →
  `channels.getChannels` → clear exit-4 error naming the account that lacks
  access); resume interrupted downloads via offset; surface
  `SessionRevokedError` as "account needs reauth" (exit 3), not a traceback.
- The 2026-07 incident case (`t.me/c/3817664407/878`, account with access)
  becomes a live phase-3 test. **Re-entry gate:** only a reproducible
  Telethon failure on current pinned Telethon re-opens TDLib, and then only
  as a measured, isolated PoC under the old stack's gate criteria
  (read-only, separate state, measured against the Telethon path).
- Existing TDLib assets are kept, not deleted: authorized TDLib sessions at
  `~/.telegram-mcp-tdlib/{main,vermassov}` and the PoC benchmark harness —
  they make a future PoC cheap.

## Consequences

- Core stays light: no tdjson binary, no second auth state per account.
- FEATURES.md unchanged: secret chats/calls remain excluded (they were
  excluded even with a TDLib backend, since it was media-only).
- Note for phase 6: the account import list (ADR-0004: main/pl/recklessou/
  teamsyncsage) does not include `vermassov`, which held the private-channel
  access in the incident. Import list to be revisited at cutover.
