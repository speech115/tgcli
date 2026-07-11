# tgcli

Stateless Telegram CLI for humans, scripts, and AI agents.
Design lineage: [openclaw/gogcli](https://github.com/openclaw/gogcli) (architecture),
`tools/telegram` (domain knowledge, sessions, TDLib media experience).

```
tg [global-flags] <command> [subcommand] [options]

tg dialogs --json
tg read @channel --limit 20 --json
tg search @chat "invoice" --json
tg send @user --preview "text"   # two-step: preview → commit
tg media download <t.me/link>
tg export subscribers @channel --output subscribers.csv
```

## Principles (non-negotiable)

1. **CLI is the product.** One entrypoint, stateless per invocation.
   No daemons, no ports, no LaunchAgents. An MCP server is a **non-goal** for v1
   (same position as gogcli) — agents call `tg ... --json` through their shell tool.
2. **Automation contract.** Data goes to stdout (`--json` / `--plain`), everything
   human goes to stderr. Documented exit codes. Additive-only JSON changes.
   See [docs/CONTRACT.md](docs/CONTRACT.md).
3. **Task-first commands.** Commands solve tasks ("read today's messages"),
   not mirror raw MTProto methods.
4. **Safety is explicit.** Reads are free; writes go through preview → commit
   with an audit log. `--readonly` and `TGCLI_NO_SEND=1` hard-block mutations.
5. **Docs are part of the system.** [docs/MAP.md](docs/MAP.md) is the project map,
   [docs/decisions/](docs/decisions/) holds ADRs, [docs/DEVLOG.md](docs/DEVLOG.md)
   records every working session. Agents must keep them current (see AGENTS.md).

## Status

v1 complete — all phases 0–7 done, project in maintenance mode. Phase 5
exported 14,296 messages from a public channel through a live Telethon
takeout session without FloodWait failures; Phase 7 adds a fail-closed
coverage gate for all 23 namespaces in the pinned Telethon layer.
Master plan: [docs/PLAN.md](docs/PLAN.md).

CI runs `pytest` and the coverage gate on every push and PR
([.github/workflows/ci.yml](.github/workflows/ci.yml)).
`scripts/bench.py` benchmarks every command against a live account
(13 steps, ~20 s; latest run: 13/13 PASS).
