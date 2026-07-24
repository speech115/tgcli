# Edit and delete messages

Both mutations follow the same preview → commit shape as
[send](send.md): `--preview` stages the change and returns a single-use
`preview_id`, then `--commit PREVIEW_ID` applies it.

## Preview an edit

```bash
tg --json edit CHAT ID "TEXT" --preview
```

| Flag | Effect |
| --- | --- |
| `--format {plain,md,html}` | rich-text format of TEXT; default `plain` |

Unlike `send` (default `md`), `edit` defaults to `--format plain`: a surgical
edit stays literal — `*`, `_`, `<` survive verbatim — unless you explicitly
ask for `md` or `html`. See [formatting.md](formatting.md) for the full
entity set under `html`.

```bash
tg --json edit --commit PREVIEW_ID
```

### Edit preview JSON

```json
{"preview_id":"p_9f3a","message_id":42,"old_text":"before","text":"after","expires_at":"2026-07-06T12:05:00+00:00"}
```

The preview returns both the previous (`old_text`) and requested (`text`)
body so the caller can confirm the diff before committing.

### Edit commit JSON

```json
{"preview_id": "p_9f3a", "message_id": 42}
```

## Preview a delete

```bash
tg --json delete CHAT ID --preview
```

| Flag | Effect |
| --- | --- |
| `--preview` | stage the deletion, return a `preview_id` |
| `--commit PREVIEW_ID` | delete the previewed message |

```bash
tg --json delete --commit PREVIEW_ID
```

### Delete preview JSON

Delete previews return the target message's `preview_id`, `message_id`, and
`text`, with the same five-minute `expires_at` as edit:

```json
{"preview_id":"p_9f3a","message_id":42,"text":"before","expires_at":"2026-07-06T12:05:00+00:00"}
```

### Delete commit JSON

```json
{"preview_id":"p_9f3a","message_id":42}
```

## Shared rules

Each command accepts either its complete preview arguments with `--preview`
or only `--commit PREVIEW_ID`. Passing a preview id of the wrong kind (e.g.
committing a `send` preview through `tg edit --commit`) is blocked before
configuration or session work and does not consume the preview.

Both commands use the same five-minute `.json` → `.pending` → `.used`
lifecycle as `send`: a failed commit (exit 1) may be safely re-run with the
same `preview_id`; do not create a second preview. Unlike `send`/`forward`,
edit and delete commits carry no Telegram `random_id` — there is no
network-level dedup — but the pre-dispatch audit record (`edit` or `delete`)
and the post-success record (`edit-result` or `delete-result`) still make a
retried commit safe to re-issue after a network or runtime failure. As with
every mutation, do not retry exit 2 (safety block) until the safety condition
changes, and do not retry exit 5 (rate limited) before its `retry_after`
elapses.

## See also

- [send.md](send.md) — full preview/commit/retry mechanics
- [forward.md](forward.md) — the same shape for forwarding
- [formatting.md](formatting.md) — `--format` values and defaults per command
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes and exit codes
