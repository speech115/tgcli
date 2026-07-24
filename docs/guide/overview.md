# Overview

`tgcli` is a stateless Telegram CLI. Its one entrypoint, `tg`, signs in as
your own Telegram user account over MTProto (via `telethon`) and gives you
JSON-first reading, search, media, export, and safe correspondence from the
command line — built for humans, scripts, and AI agents alike.

## Execution model

Every invocation does exactly one operation: connect, do the task,
disconnect, exit. Nothing runs between invocations — there is no daemon, no
listening port, and no LaunchAgent. This is a deliberate choice
([ADR-0002](../decisions/ADR-0002-cli-first-stateless.md)): the prior stack
was daemon-first, and its ports, background auth state, and drift-detection
tooling were the source of most reliability incidents. The cost is one MTProto
handshake per call (`scripts/bench.py` walks 13 commands in about 20 seconds
against a live account); the benefit is that a `tg` process can never be "not
running" or "stuck" between calls, and long operations (downloads, exports)
have no artificial time cap.

```bash
tg --json dialogs --limit 10
```

## Streams

- **stdout** carries contract data only. With `--json`, exactly one JSON
  document. With `--plain`, stable TSV rows (frozen column order; new
  columns only ever append). Without either flag, stdout is a human-readable
  table meant for a terminal, not a script.
- **stderr** carries everything else: progress, hints, warnings, and error
  messages.
- When a command fails under `--json`, the same error is also mirrored to
  stderr as one single-line JSON object, so a caller that captured only
  stderr (or only stdout) can still recover the structured cause:

  ```json
  {"error": {"code": "FLOOD_WAIT", "message": "...", "retry_after": 42}}
  ```

Full stream and stability rules: [CONTRACT.md §2-3](../CONTRACT.md).

## Exit codes

| Code | Meaning | Typical cause |
| --- | --- | --- |
| 0 | success | |
| 1 | runtime error | network failure, unexpected exception |
| 2 | blocked by safety policy | `--readonly` + a mutation, `TGCLI_NO_SEND` |
| 3 | config or auth error | missing account, dead session, bad api_id, busy session lock |
| 4 | not found | unknown dialog, message id, media |
| 5 | rate limited | FloodWait longer than threshold; `retry_after` in the error JSON |

Never parse human-readable output — check the exit code and, for scripting,
always pass `--json` or `--plain`.

## Config and state

Two directories, never mixed:

| Path | Contents |
| --- | --- |
| `~/.config/tgcli/config.toml` | Account registry: `api_id`, `api_hash`, `session` name, `default_account`. |
| `~/.local/state/tgcli/` (mode `0700`) | Session files and their locks, preview records, `audit.jsonl`, the invocation journal, and download/cache artifacts. |

Both paths can be overridden — `TGCLI_CONFIG` for the config file,
`TGCLI_STATE_DIR` for the state root. See
[install.md](install.md) for how to create the config file and
[accounts.md](accounts.md) for how account entries map onto session files.

## Safety posture

Reads are always allowed, including under `--readonly`; the only local state
they write is the entity cache and the metadata-only invocation journal.
Every mutation — sending, editing,
deleting, forwarding, drafting — is a two-step preview → commit: the first
call resolves the target and writes a single-use, 5-minute preview record;
the second commits it by id, so what you reviewed is exactly what goes out.
`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` hard-block commits
before any network work. Full detail, including which commands are
content-free direct mutations (no preview) and how the audit log works, is
in [safety.md](safety.md).

## Where to go next

- [install.md](install.md) — install `tgcli`, put `tg` on your PATH, configure an account.
- [quickstart.md](quickstart.md) — a first session, one command at a time.
- [accounts.md](accounts.md) — multiple accounts, selection order, session locks.
- [safety.md](safety.md) — the preview → commit contract and the readonly/no-send gates in full.
- [README.md](README.md) — the full page index: reading, correspondence, data, and operations.
