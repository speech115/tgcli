# ADR-0124: `tg-serve` runs many commands over one held session

Date: 2026-11-10
Status: proposed (owner request of 2026-11-10); accepted when merged
Amends: ADR-0002 (one process per command) for a parent-owned adapter process

## Context

An adapter that turns agent tool calls into `tg` commands (an MCP server, for
example) pays the per-invocation cost ADR-0002 accepted on every call. Measured
on the owner's OnePlus 7 Pro (Termux, 2026-11-09): a `tg read` process takes
~2.4 s, of which ~1.1 s is importing tgcli and ~1.2 s is connecting the session;
the same read on an already connected client takes ~0.15 s of Telegram time. An
agent answering one question makes several calls, so the cost multiplies.

The owner's remote MCP server (`tg-mcp` in a separate repository, not the
ADR-0123 adapter) worked around this with a worker that imported tgcli and
replaced `session.client`, `asyncio.run` and `cli.build_parser` from outside.
That is fragile: any change to those internals breaks it silently. The owner
asked to move the worker into tgcli as a supported mode.

## Decision

1. **`tg-serve` is a stdio process owned by its parent**, like `tg-mcp`: it
   opens no socket, writes no state of its own, and exits when stdin closes.
   It holds one account's session (optionally a role, ADR-0062) for its life.
2. **Every request is a full `tg` invocation through `cli.main`**, so parsing,
   preflight, the readonly gates, preview -> commit, audit, the invocation
   journal and the governor behave exactly as in a `tg` process. The server
   fixes `--account`/`--session-role` (a request that sets them is refused) and
   adds `--json`.
3. **Three seams, in tgcli itself:**
   - `session.hold(account, role=)` opens the session once; every
     `session.client()` for the same file inside it gets the held client. A
     mutation still runs with Telethon retries off, a dropped connection is
     reconnected, and a revoked session still reads as reauthentication.
   - `tgcli.loop.run` replaces the direct `asyncio.run` calls of a command
     step; `tg-serve` points it at its one long-lived loop, because a Telethon
     client belongs to the loop it connected on.
   - `cli.main(argv, parser=)` takes a parser built once.
4. **stdin is the request pipe**, so a command that reads stdin (`run -`,
   `--code -`, `--password-stdin`) reads the request's `stdin` string instead.

## Consequences

- One call costs the command's own Telegram time plus ~0.2 s of tgcli work:
  ~0.37 s for a read on the phone instead of ~2.4 s.
- While a `tg-serve` runs, its session file stays locked; other `tg` processes
  on that file wait or fail as they would against any long command. Give the
  server its own role (`tg accounts login ALIAS --role NAME`).
- Process-wide state now spans requests: the governor ledger stays open, and a
  module-level cache warmed by one command is seen by the next. Commands were
  written for one invocation per process; a command that keeps such state and
  breaks under `tg-serve` is a bug in that command.
- `tg` itself is unchanged: `loop.run` defaults to `asyncio.run`, `main`
  builds its parser, and no session is held.
