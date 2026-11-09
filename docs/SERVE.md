# Many commands over one session (`tg-serve`)

`tg-serve` is for programs that run many `tg` commands in a row, such as an
MCP server: it keeps one session connected so each command skips the import
and connect a `tg` process pays (~2 s on a phone). Why and what it keeps
unchanged: [ADR-0124](decisions/ADR-0124-held-session-serve.md).

## Run it

```bash
tg-serve [--account main] [--session-role NAME]
```

Give it its own session role (`tg accounts login main --role NAME --phone …`):
the held session file stays locked while the server runs.

## Protocol

One JSON object per line. After connecting, the server writes:

```json
{"ready": true, "account": "main", "role": "mcp"}
```

Each request names a `tg` command without global flags; the server adds
`--account`, `--session-role` and `--json`. Put positional values after `--`
so text that starts with `-` is never read as a flag:

```json
{"argv": ["read", "--limit", "5", "--", "@channel"], "stdin": ""}
```

The reply carries what that `tg` invocation would have printed and its exit
code ([CONTRACT](CONTRACT.md) §4):

```json
{"exit_code": 0, "stdout": "{\"dialog\": …}\n", "stderr": ""}
```

- `stdin` (optional) is what the command reads from stdin, for `run -`,
  `--code -` or `--password-stdin`.
- A request that sets `--account` or `--session-role`, or is not a JSON object
  with a non-empty `argv` list, gets exit code 1 and a `USAGE` error.
- If the session cannot be opened, the server writes the error envelope to
  stdout instead of the ready line and exits with its code (3 for an
  unauthorized or busy session).
- Requests run one at a time, in order. Closing stdin ends the server and
  releases the session.
