# ADR-0002: Stateless CLI core; no daemons; MCP is a v1 non-goal

Status: accepted (2026-07-06)

## Context
The old stack is daemon-first: 4 MCP daemons on ports 8799–8802,
LaunchAgents, auth tokens, a control-plane to detect drift, plugin-cache
parity checks. Every reliability incident traced back to this layer
("ports die with session", 120s MCP call cap, daemon not running/authorized).
gogcli explicitly lists an MCP server as a non-goal; agents use the CLI.

## Decision
`tg` is a stateless process: connect → do the task → disconnect → exit.
No background processes, no listening sockets. Agents call `tg ... --json`
through their shell tool. An MCP server may be reconsidered later only as a
thin adapter over the same command layer, with evidence it's needed, via ADR.

## Consequences
- Pays 1–3 s MTProto handshake per invocation (entity cache keeps resolves
  cheap). Accepted; see PLAN.md risks.
- The entire control-plane/drift class of tooling becomes unnecessary.
- Long operations (downloads, exports) have no 120s cap — the process
  lives as long as the task.
