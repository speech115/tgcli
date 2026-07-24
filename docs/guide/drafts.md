# Drafts

A Telegram draft is the text sitting in a dialog's input box, synced across
every device on the account. Telegram keeps exactly one draft per dialog and
no history — saving a new draft silently overwrites whatever the human had
half-written there, with no undo.

`tg draft` lets an agent leave a prepared message for a human to review and
send themselves, instead of sending on their behalf: there is deliberately no
`draft send`. This is a useful alternative to [send](send.md) whenever the
agent should propose text but not have the authority to publish it — the
final press stays with the human. See
[ADR-0039](../decisions/ADR-0039-message-drafts.md) for the full rationale.

## Show a draft

```bash
tg --json draft show CHAT
```

```json
{"draft": {"chat": {"id": 111, "name": "Alice"}, "text": "hello",
 "custom_emoji": [], "reply_to_msg_id": null, "topic_id": null,
 "date": "2026-07-23T12:00:00+00:00", "is_empty": false}}
```

An empty or missing draft is still success: `text: ""`, `is_empty: true`,
`date: null`. `date` is the draft's last-edited time, not a send time.
`custom_emoji` follows the same `{id, emoji, offset, length}` shape as
messages — see [formatting.md](formatting.md).

## List every draft

```bash
tg --json draft list
```

```json
{"drafts": [{"chat": {"id": 111, "name": "Alice"}, "text": "hello",
             "custom_emoji": [], "reply_to_msg_id": null, "topic_id": null,
             "date": "2026-07-23T12:00:00+00:00", "is_empty": false}]}
```

`list` returns every non-empty draft on the account. `show` and `list` are
typed read operations: they work under `--readonly`/`TGCLI_READONLY=1` and
inside `tg batch`.

## Set a draft (preview → commit)

```bash
tg --json draft set CHAT "TEXT" --preview
tg --json draft set --commit PREVIEW_ID
```

| Flag | Effect |
| --- | --- |
| `--format {plain,md,html}` | rich-text format of TEXT; default `md` |
| `--reply-to MESSAGE_ID` | attach a reply header to the draft |
| `--topic TOPIC_ID` | forum topic id |

`set` mirrors `send`'s formatting defaults and reply/topic flags on purpose —
diverging defaults between "write a message" and "write a draft of a message"
would be a trap. `--file` is out of scope for v1.

### Set preview JSON

```json
{"preview_id":"p_9f3a","to":{"id":111,"name":"Alice"},"old_text":"half-written",
 "text":"**reply**","format":"md","reply_to":42,"topic":null,
 "expires_at":"2026-07-23T12:05:00+00:00"}
```

`old_text` is the current draft body that this commit will overwrite —
inspect it before committing so a half-written human draft is not discarded
by accident.

## Clear a draft (preview → commit)

```bash
tg --json draft clear CHAT --preview
tg --json draft clear --commit PREVIEW_ID
```

### Clear preview JSON

A clear preview carries `old_text` and `to` only:

```json
{"preview_id":"p_9f3a","to":{"id":111,"name":"Alice"},"old_text":"half-written",
 "expires_at":"2026-07-23T12:05:00+00:00"}
```

## Commit JSON (set and clear)

Both commits return the resulting draft object:

```json
{"preview_id":"p_9f3a","draft":{"chat":{"id":111,"name":"Alice"},"text":"**reply**",
 "custom_emoji":[],"reply_to_msg_id":42,"topic_id":null,
 "date":"2026-07-23T12:05:30+00:00","is_empty":false}}
```

`--commit` accepts only a preview id and validates its `expected_kind`
(`draft-set` or `draft-clear`) — committing a preview of the wrong kind is
blocked before configuration or session work.

## Safety and the race window

Preview creation is non-mutating and permitted under readonly gates; commit is
blocked by `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1`. Authorised
commits append a `draft-set` or `draft-clear` audit record before the network
call, and `draft-set-result` / `draft-clear-result` after success.

Immediately before saving, commit re-reads the complete observable draft
state — text, reply, topic, and every formatting entity — and fails closed
(exit 2) if it no longer matches the preview's internal snapshot; a matching
observed state is treated as the successful retry of an already-applied save.
Telegram exposes no conditional-save/version token, so an edit made by the
human after that re-read and before the save remains a residual race —
make a new preview after any blocked commit rather than reusing it.

## See also

- [send.md](send.md) — the flagship preview/commit/retry mechanics
- [formatting.md](formatting.md) — `--format` values and the custom-emoji shape
- [../decisions/ADR-0039-message-drafts.md](../decisions/ADR-0039-message-drafts.md) — why drafts exist and their risk model
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes and exit codes
