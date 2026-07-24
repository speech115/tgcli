# Forward a message

`forward` copies one existing message from a source chat to a destination
chat using Telegram's native forward request. It follows the same
preview → commit shape as [send](send.md).

## Preview a forward

```bash
tg --json forward SOURCE ID DESTINATION --preview
```

| Flag | Effect |
| --- | --- |
| `--preview` | resolve source/destination, stage the forward, return a `preview_id` |
| `--commit PREVIEW_ID` | forward the previewed message |

Preview resolves both `SOURCE` and `DESTINATION` and reads the source message
as a preflight only; nothing is sent yet.

## Commit the preview

```bash
tg --json forward --commit PREVIEW_ID
```

Commit re-resolves the stored source and destination chat references into
input peers, then sends the source message to the destination through
Telegram's native forward request. A native forward takes no `--reply-to` or
`--topic` flag and creates no reply header.

## Preview JSON

```json
{"preview_id":"p_9f3a","source":"@source","message_id":42,"destination":"@destination","text":"hello","expires_at":"2026-07-06T12:05:00+00:00"}
```

## Commit JSON

```json
{"preview_id":"p_9f3a","message_id":43}
```

`message_id` in the commit response is the id of the new forwarded message in
the destination chat, distinct from the source `message_id` in the preview.

## random_id confirmation

The stored forward payload also carries a positive Telegram `random_id`
alongside the submitted source and destination references. Commit returns its
`{"preview_id", "message_id"}` result only after exact `random_id`
confirmation from Telegram — the same idempotency mechanism `send` uses. If a
commit fails on a network or runtime error (exit 1), re-run the same
`--commit PREVIEW_ID`: Telegram deduplicates by `random_id` within the
preview's five-minute TTL, so the retry confirms the original forward instead
of creating a duplicate. Never create a second preview to retry a failed
commit.

Forward previews and commits use the same validation, readonly gates
(`--readonly`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1` block `--commit` only),
retryable `.json` → `.pending` → `.used` lifecycle, and fail-closed `forward`
/ `forward-result` audit records as send. As with every mutation, do not
retry exit 2 until the safety condition changes, and do not retry exit 5
before its reported `retry_after` elapses.

## See also

- [send.md](send.md) — full preview/commit/retry mechanics and TTL details
- [editing.md](editing.md) — the same shape for `edit`/`delete`
- [safety.md](safety.md) — readonly/no-send gates and audit records
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes and exit codes
