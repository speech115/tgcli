# Batch reads

`tg batch` runs a list of read-only operations from JSONL on stdin under a
single Telegram session, and writes one JSON result per line to stdout. Use
it instead of many separate `tg` invocations when a caller needs several
reads and wants to pay the session-startup cost once (ADR-0032).

```bash
tg batch --fail-fast < ops.jsonl
```

| Flag | Effect |
| --- | --- |
| `--fail-fast` | stop after the first failed op instead of running the rest |

## Input

Each stdin line is a JSON object with an `op` field plus that op's own
arguments. Blank lines are ignored and do not count toward the op cap.

```jsonl
{"op": "dialogs", "unread_only": true, "kind": "channel"}
{"op": "read", "chat": "@channel", "limit": 20, "before_id": 42}
{"op": "search", "chat": "@channel", "query": "hello", "from": "@alice"}
{"op": "search", "all": true, "query": "hello", "limit": 20}
{"op": "latest", "chat": "@channel"}
{"op": "message", "chat": "@channel", "message_id": 42, "context": 3}
{"op": "info", "chat": "@channel", "full": true}
{"op": "count", "chat": "@channel"}
{"op": "resolve", "ref": "@alice"}
{"op": "mutual-chats", "ref": "@alice"}
{"op": "contacts.list"}
{"op": "contacts.search", "query": "ali", "global": true}
{"op": "media.manifest", "source": "@channel", "type": "photo", "limit": 50}
{"op": "thread", "chat": "@channel", "message_id": 42, "replies": true, "depth": 10}
{"op": "draft.show", "chat": "@channel"}
{"op": "draft.list"}
```

Each op's fields and defaults match its standalone command (for example
`read`'s `limit` still defaults to 20, `thread`'s `depth` still defaults to
20). ISO date fields (`since`/`until` on `read`, `since` on `search` and
`media.manifest`) parse the same way as their standalone commands.

## Allowed operations

Exactly this set, and nothing else: `dialogs`, `read`, `search`, `latest`,
`message`, `info`, `count`, `resolve`, `mutual-chats`, `contacts.list`,
`contacts.search`, `media.manifest`, `thread`, `draft.show`, `draft.list`.

Any other `op` value — including every mutation, `doctor`, `export`,
`clone`, `media.download`, `api`, and `accounts` — is rejected with exit 2
before the session even opens.

## Caps

Hard cap of **100** ops per invocation. An input with more than 100
non-blank op lines is rejected with exit 2 before any network work — it
never runs 100 ops and silently drops the rest.

## Output

Stdout is one JSON object per input op, in order:

```json
{"ok": true, "op": "read", "data": {"dialog": {...}, "messages": [...], "page": {...}}}
{"ok": false, "op": "count", "error": {"code": "NOT_FOUND", "message": "dialog not found: '@nope'"}}
```

`data` on a success line is exactly that op's normal `--json` payload (the
same `dialogs`/`read`/`search`/etc. shape documented on the other guide
pages). `error` on a failure line carries the same `code`/`message` fields
as a standalone command's JSON error, plus any extra details such as
`retry_after` for a rate limit.

## Partial failures

Ops run sequentially against one session. By default every op runs even if
earlier ones failed, and the complete JSONL output is written; `--fail-fast`
stops after the first failure instead, leaving later ops unattempted (and
absent from stdout).

Process exit is **0 only if every op succeeded**. Otherwise the process
exits with the *first* failing op's own exit code (for example 4 if the
first failure was a not-found chat, 5 if it was a rate limit) — check
individual `"ok"` fields in the output to see which specific ops failed
when more than one did.

## See also

- [read.md](read.md) — `read`, `message`, `thread` as standalone commands
- [search.md](search.md) — `search` as a standalone command
- [contacts.md](contacts.md) — `resolve`, `contacts`, `mutual-chats` as standalone commands
- [../CONTRACT.md](../CONTRACT.md) — §5.0 batch contract
- [../decisions/ADR-0032-data-plumbing-inbox-ergonomics.md](../decisions/ADR-0032-data-plumbing-inbox-ergonomics.md) — why batch is read-only and capped at 100
