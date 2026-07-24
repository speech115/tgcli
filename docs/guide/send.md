# Send a message

Send is the two-step mutation: `--preview` stages the message and returns a
single-use `preview_id`, then `--commit PREVIEW_ID` actually dispatches it.
Nothing reaches Telegram until commit.

## Preview a text send

```bash
tg --json send CHAT "TEXT" --preview
```

| Flag | Effect |
| --- | --- |
| `--format {plain,md,html}` | rich-text format of TEXT/caption; default `md` |
| `--reply-to REPLY_TO` | reply to this message id |
| `--topic TOPIC` | forum topic id |
| `--silent` | send without a notification |
| `--file FILE` | send a file instead of/with text (see below) |
| `--caption CAPTION` | caption for `--file`; requires `--file` |

`send` accepts `--format {plain,md,html}`, defaulting to `md` (Telethon
Markdown, matching the historical client default). `plain` sends TEXT
verbatim with no entities; `html` uses the full entity set described in
[formatting.md](formatting.md). The commit re-renders from the stored format
and passes explicit entities — the preview only stores the raw markup.

## Commit the preview

```bash
tg --json send --commit PREVIEW_ID
```

## File sends

```bash
tg --json send CHAT --file /path/to/photo.jpg --caption "TEXT" --preview
```

`--caption` requires `--file`; a file send cannot also take positional text.
Immediately before upload, commit opens the source file once, copies it into
a unique temporary snapshot, and recomputes its size and SHA-256 digest
against the values captured at preview time. A mismatch is blocked (exit 2);
only the verified snapshot is uploaded, so replacing the original file after
previewing cannot change what gets sent. The snapshot is removed after
success or any failure.

## Preview JSON

```json
{"preview_id": "p_9f3a", "to": {"id": 111, "name": "Alice"},
 "text": "hello", "file": null, "file_size": null, "file_sha256": null,
 "reply_to": null,
 "topic": null, "silent": false, "expires_at": "2026-07-06T12:05:00+00:00"}
```

For a file preview, `text` carries the optional caption, `file` is the
absolute source path, `file_size` is its byte size, and `file_sha256` is the
lowercase SHA-256 digest of the same open byte stream. The preview also
stores the target, `kind: "send"`, the chosen `format`, and a positive
Telegram `random_id` used by the retry rule below — none of that is echoed
back in the printed preview object above except through `--commit`'s result.

## Commit JSON

```json
{"preview_id": "p_9f3a", "message_id": 42}
```

## The retry rule

Create exactly one preview per message. If `--commit PREVIEW_ID` fails with a
network or runtime error (exit 1), re-run that same `--commit PREVIEW_ID` —
never create a second preview for the same message. The preview stores a
Telegram `random_id` alongside the send payload; when tgcli retries the
commit, Telegram deduplicates by that `random_id` within the preview's TTL,
so a retried commit confirms the original send instead of dispatching a
duplicate message. Only a confirmed send marks the preview used, and only
after its result audit record persists — a preview that failed mid-flight
stays retryable.

Two exit codes must not be blindly retried:

- **Exit 2** (blocked by safety policy — `--readonly`, `TGCLI_READONLY=1`,
  `TGCLI_NO_SEND=1`) — retrying does nothing until the safety condition
  itself is intentionally changed.
- **Exit 5** (rate limited) — wait for the `retry_after` seconds reported in
  the JSON error before retrying.

## TTL and single use

Previews expire five minutes after creation (`expires_at`). A preview moves
through `.json` → `.pending` → `.used` on disk; a failed commit leaves it in
`.pending` and eligible for retry, while a confirmed commit consumes it
permanently. A commit against an expired or already-used `preview_id` fails
closed. `--preview` itself is non-mutating — it may resolve the chat and
write the local preview record, but never sends anything — so it remains
permitted under `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1`; those
gates apply only to `--commit`.

## See also

- [formatting.md](formatting.md) — `--format` values, HTML entities, custom emoji
- [safety.md](safety.md) — readonly/no-send gates and audit records
- [editing.md](editing.md) — same preview → commit shape for `edit`/`delete`
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes and exit codes
