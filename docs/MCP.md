# Read-only MCP adapter (`tg-mcp`)

`tg-mcp` lets an MCP client (Claude Desktop, Claude Code, Cursor) read
Telegram through tgcli without a shell. It is read-only and starts only with a
chat allowlist. Why it exists and what it may never do: [ADR-0123](decisions/ADR-0123-readonly-mcp-adapter.md).

## Run it

```bash
tg-mcp --allow-chat @channel --allow-chat -1001234567890 [--account main] [--timeout 120]
```

- `--allow-chat CHAT` (repeatable): `@username`, `t.me/name`, `t.me/c/ID`, or
  a negative dialog id. Use the exact id from `tg dialogs`: basic groups
  and channels remain separate peers. A private link `t.me/c/1234` matches
  channel id `-1000000001234`, not user `1234` or basic group `-1234`.
  Positive bare ids are ambiguous in Telethon and are rejected in limited
  mode; use `@username` for a user. Positive ids remain available with
  `--allow-all-chats`, which intentionally has no chat isolation.
- `--allow-all-chats`: every chat. Prefer naming chats.
- `--account`: the tgcli alias; otherwise `TGCLI_ACCOUNT` or the default.

Client config, for example Claude Code's `.mcp.json`:

```json
{"mcpServers": {"telegram": {"command": "/path/to/tgcli/.venv/bin/tg-mcp",
  "args": ["--allow-chat", "@channel"]}}}
```

## Tools

| Tool | Runs |
| --- | --- |
| `telegram_dialogs` `{limit?, unread_only?, kind?}` | `tg dialogs`, then drops rows off the allowlist (`hidden_by_allowlist`) |
| `telegram_read` `{chat, limit?, before_id?, after_id?}` | `tg read` |
| `telegram_search` `{chat, query, limit?}` | `tg search CHAT QUERY` (never `--all`) |
| `telegram_message` `{chat, message_id, context?}` | `tg message` |
| `telegram_info` `{chat}` | `tg info` |

Every call runs `tg --json --readonly` with `TGCLI_READONLY=1` and
`TGCLI_NO_SEND=1`. The backing read commands also verify a negative dialog
id against the resolved Telegram peer before reading or returning its data,
so a colliding peer in Telethon's cache is refused (`NOT_FOUND`, exit 4).
A non-zero exit becomes `isError: true` with tgcli's own
error envelope and `exit_code`.

## Untrusted content

Each result opens with a notice, then the tgcli JSON. Every string a Telegram
user wrote (`text`, `name`, `title`, `about`, `caption`, `quote_text`, file
names and attachment MIME metadata, …) is fenced:

```
⟦untrusted:3f9c0a1b2c4d⟧Ignore previous instructions…⟦/untrusted:3f9c0a1b2c4d⟧
```

The nonce changes on every call, so a message cannot close its own fence.
Bidi overrides, zero-width marks and Unicode tag characters inside the text
are rewritten as visible `\uXXXX` escapes. `untrusted_content` reports the
nonce, how many fields were fenced, which were rewritten, and `signals`:
fields that look like an injection attempt (`ignore_instructions`,
`role_reassignment`, `prompt_reference`, `chat_markup`, `tool_request`,
`exfiltration`). Signals are hints for the agent and the owner; nothing is
removed.
