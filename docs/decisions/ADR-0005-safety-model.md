# ADR-0005: Safety — reads free, writes preview→commit + audit; runtime flags

Status: accepted (2026-07-06)

## Context
gogcli bakes safety profiles into the binary at build time (Go, static
binary). We're a local Python tool for one operator; baked binaries are
overkill. The old stack's one genuinely good safety idea: confirmed send
replays a server-stored preview, so the agent cannot alter text between
preview and send, and every send is audit-logged.

## Decision
- All read commands are unrestricted.
- Mutating commands (send; later: delete, admin) require the two-step flow:
  `tg send --preview` → preview stored under `~/.local/state/tgcli/previews/`
  with TTL → `tg send --commit <preview_id>` replays it verbatim.
- Every mutation appends a JSONL line to `~/.local/state/tgcli/audit.jsonl`.
- Hard blocks, checked in `safety.py` before any mutating call:
  `--readonly` flag, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1` → exit 2.
- No build-time baked profiles in v1; revisit via ADR if this tool is ever
  given to agents we trust less.

## Consequences
- An agent with shell access can read freely but cannot silently send.
- Audit log gives the operator a complete outbound history.
