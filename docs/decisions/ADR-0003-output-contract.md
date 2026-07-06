# ADR-0003: Output contract — stdout=data, stderr=human, fixed exit codes

Status: accepted (2026-07-06)

## Context
gogcli's most agent-valuable property is its automation contract: stable
`--json`/`--plain` on stdout, prompts/progress on stderr, documented exit
codes (2 = policy block). The old stack had no output contract at all —
MCP tool schemas served that role and died with the daemons.

## Decision
Adopt the full contract in docs/CONTRACT.md: `--json` (one JSON document),
`--plain` (frozen-column TSV), stderr for everything human, exit codes
0/1/2/3/4/5 (success/runtime/policy/config-auth/not-found/rate-limit).
JSON evolution is additive-only; breaking changes need ADR + major bump.

## Consequences
- Commands return data structures; only `cli.py` prints (testable contract).
- `errors.py` is the single source of exit codes.
