# Change feed

Foreground, daemonless Telegram updates via `tg changes`
([ADR-0063](../decisions/ADR-0063-tg-changes-design.md),
[ADR-0103](../decisions/ADR-0103-account-bound-changes-cursors.md)). The
account-bound cursor is the only state and lives with the caller — tgcli stores
nothing between invocations.

## Baseline

```bash
tg --json changes --init
tg --json changes --init --peer @channel --peer -1001234
```

Returns `{"events": [], "next_cursor": "v2:…", "gap": null, "skipped": {}}`.
The opaque `v2:` value authenticates its full common + channel state for the
selected configured account. Each `--peer` must be a channel/supergroup; it is
baselined at the current `pts` with **no history replay** (stderr note). Private
dialogs use the common updates tier automatically.

## Poll

```bash
tg --json changes --cursor "$CURSOR"
tg --json --session-role job changes --cursor "$CURSOR" --wait 30
```

`--wait N` long-polls up to N seconds for the first event, then settles
for a fixed 2 seconds (never exceeding N) to batch a burst. Intended to
run on a named session role so the primary stays free for interactive
commands ([ADR-0062](../decisions/ADR-0062-job-session-role.md)). Roles on the
same configured account share cursor binding; another `--account` does not.

Legacy `v1:` cursors, modified payloads, and cursors from another account are
refused with exit 2 before polling. Run `tg --json changes --init` to establish
a fresh baseline; tgcli never blesses an old or caller-edited cursor.

## Subscriptions mid-stream

```bash
tg --json changes --cursor "$CURSOR" --peer @other
tg --json changes --cursor "$CURSOR" --drop-peer @other
```

Adds/removes channel subscriptions stored inside the authenticated cursor. A
new subscription starts at the current `pts` (stderr note; no replay). Editing
the opaque channel map directly invalidates the cursor and is refused.

## Events and gaps

| Type | Meaning |
| --- | --- |
| `message_new` / `message_edit` | Full `tg read` message body (`truncated: true` when Telegram sent a short form). |
| `message_delete` | Tombstone with `ids` only. |
| `channel_activity` | Unsubscribed channel changed — ids only. |

A gap (`differenceTooLong` / `channelDifferenceTooLong`) is exit 0 with a
`gap` object naming its scope and an honest `recover` hint. Creation
history can be rebuilt with `tg read CHAT --after-id N`; edits/deletes in
the gap window are lost. Unhandled update classes are counted in
`skipped`.

See [CONTRACT.md §12](../CONTRACT.md).
