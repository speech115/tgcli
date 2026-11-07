# ADR-0123: A read-only MCP adapter over the command layer

Date: 2026-11-07
Status: proposed (owner request of 2026-11-07); accepted when merged
Amends: ADR-0002, which kept MCP out of v1 and allowed it later "only as a
thin adapter over the same command layer, with evidence it's needed, via ADR"

## Context

Agents that speak MCP but have no shell tool cannot use tgcli at all, and
agents that do have a shell get the whole CLI, writes included, gated only by
`TGCLI_READONLY`. The owner asked on 2026-11-07 for three things for agent
use: an MCP server that is read-only by default with a chat allowlist, a layer
that marks Telegram text as untrusted before an agent reads it, and fuzz tests
for broken responses (the last is a separate change).

ADR-0002's reasons still hold: the old stack died of long-lived daemons on
ports, auth tokens between them, and a 120 s call cap. An adapter must not
bring any of that back.

## Decision

1. **`tg-mcp` is a stdio server owned by its MCP client.** It opens no socket,
   writes no state, and exits when stdin closes. It is a second console script
   in the same package, with no new dependency: the JSON-RPC loop is
   hand-written (`src/tgcli/mcp/server.py`).
2. **Every tool call is one `tg` process.** The adapter runs
   `tg --json --readonly …` under its own interpreter, with
   `TGCLI_READONLY=1` and `TGCLI_NO_SEND=1` forced, and returns the JSON
   document. No Telegram session is held between calls; the CLI's preflight,
   readonly gate and exit codes stay the only authority.
3. **Five read tools and nothing else:** `telegram_dialogs`, `telegram_read`,
   `telegram_search` (scoped only, no `--all`), `telegram_message`,
   `telegram_info`. Adding a write tool needs a new ADR.
4. **A chat allowlist is mandatory.** The server refuses to start without
   `--allow-chat CHAT` or an explicit `--allow-all-chats`. A chat off the list
   never reaches `tg`; `telegram_dialogs` drops rows that are not on it and
   reports how many it hid.
5. **Arguments cannot become flags.** Tool arguments are validated against a
   closed schema, and positionals follow `--` in the argv.
6. **Telegram-authored strings are fenced as untrusted**
   (`src/tgcli/mcp/untrusted.py`): each one sits between markers carrying a
   per-call random nonce, fence-lookalike and invisible characters (bidi
   overrides, zero-width marks, tag characters) are made visible, and phrases
   that look like prompt injection are listed in `untrusted_content.signals`.
   Text is never deleted. This is a marking layer, not a guarantee: the agent
   still has to honour it.

## Consequences

- Each call pays the per-invocation handshake ADR-0002 already accepted.
- The CLI contract (`docs/CONTRACT.md`) is unchanged; the adapter's own
  surface is documented in `docs/MCP.md`.
- The fencing lives only in the adapter. `tg --json` output is unchanged.
