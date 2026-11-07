"""Read-only MCP adapter over the `tg` command layer (ADR-0123).

The adapter is a stdio JSON-RPC loop owned by the MCP client. Every tool call
runs one stateless `tg --json --readonly …` process, so no Telegram session is
held between calls and the CLI's own readonly gate decides what may run.
"""
