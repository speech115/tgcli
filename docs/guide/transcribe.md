# Transcribe voice messages

`tg transcribe <chat> <message_id>` asks Telegram's server to transcribe a
voice message and waits for the result. It requires a **Premium** account; a
missing subscription is exit 2 (`BLOCKED`).

```bash
tg --json transcribe @socrates 42
```

```json
{"dialog": {"id": -1001234, "name": "Socrates"},
 "message_id": 42,
 "transcription": {"text": "Привет, как дела?", "transcription_id": 987,
                   "pending": false}}
```

The result arrives asynchronously: `transcribeAudio` returns a `pending`
update first, and the text lands through the update pipeline moments later.
The command waits for it, bounded by `--timeout` (default **120** seconds);
expiry is the normal `TIMEOUT` exit and reports the `transcription_id`, so
the call can be retried. Re-running the command on the same message is safe.

The message must be a voice note — a non-voice message is exit 4
(`NOT_FOUND`). Plain output is one row: `message_id`, `text`.

The raw method stays reachable through [`tg api`](api.md)
(`messages.transcribeAudio`, `--write --confirm`), but the raw passthrough
cannot express the async wait; prefer this command.

For local offline transcription of archived voice notes, see
[`archive transcribe`](archive.md) — that queue is separate from the
server-side transcription here.
