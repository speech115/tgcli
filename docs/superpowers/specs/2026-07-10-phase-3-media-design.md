# Phase 3 Media Download Design

**Status:** approved 2026-07-10

## Goal

Add a Telethon-only `tg media download` command that retrieves the media
attached to a specified Telegram message, writes the completed file to the
user's Downloads directory by default, and can safely resume an interrupted
download.

## Scope

The command interface is:

```text
tg media download <t.me/link|chat> [message_id] [--output PATH]
```

- A public link has the form `https://t.me/<username>/<message_id>`.
- A private link has the form `https://t.me/c/<channel_id>/<message_id>`.
- The alternate form accepts an existing tgcli chat reference followed by a
  numeric message ID.
- `--output` selects the final file path; without it, the final path is
  `~/Downloads/<sanitized Telegram filename>`.
- A final output file is never overwritten. If it already exists, the command
  stops before opening a download stream and reports the collision clearly.

The phase stays read-only. It does not add TDLib, a daemon, takeout sessions,
or new dependencies. Takeout is Phase 5; bulk download optimisation is not a
reason to change the stateless CLI boundary.

## Design Choices

Three approaches were considered:

1. `TelegramClient.download_media()` only: smallest implementation, but no
   controlled resume or parallel transfer.
2. A local Telethon streaming engine: preserve a `.part` file and compact
   state record, then continue from the known offset. This covers the
   reliability requirement without a second backend.
3. Embed TDLib: supplies a download manager, but duplicates auth and state and
   conflicts with ADR-0009.

The implementation uses option 2. It begins with one stream and a resumable
state record; a later task adds bounded parallel chunks through additional
Telethon connections and compares it to the single-stream baseline on the
same media. This makes the speed claim measurable instead of assumed.

## Components and Data Flow

1. `cli.py` parses the nested `media download` command and emits its returned
   JSON/plain data in the normal CLI pipeline.
2. `commands/media.py` parses the source, resolves the entity and message,
   validates that the message carries downloadable media, derives a safe
   filename, and returns structured result data. It never manages client
   connection lifecycle or writes to stdout.
3. A small download-state helper under `commands/media.py` persists partial
   transfer metadata below `~/.local/state/tgcli/downloads/`. State identifies
   the source message and final destination, records the completed byte
   offset, and points at a private `.part` file. It contains no API keys or
   session data.
4. `session.client(account)` supplies the authenticated connection. For a
   private `t.me/c/` link, resolution first searches dialogs, then performs
   the narrow channel lookup required by ADR-0009. If the configured account
   lacks access, the command returns exit code 4 and names that account.
5. Transfer progress is emitted only to stderr. On success the `.part` file
   is atomically moved to the final destination and state is deleted.

## Errors and Safety

- Malformed links, unresolved chats, unknown messages, and messages without
  downloadable media use the existing not-found contract (exit 4).
- `SessionRevokedError` is translated to a configuration/auth error (exit 3)
  that tells the user to reauthenticate; no traceback leaks to stdout.
- Existing final paths are refused without overwrite. Existing compatible
  partial state is resumed; incompatible or corrupt state is rejected rather
  than guessed over.
- Telegram-provided filenames are treated as untrusted input: path separators
  and control characters cannot escape the selected destination.
- JSON output describes the source, final path, byte count, and whether the
  transfer resumed. Plain output uses a fixed TSV shape. CONTRACT.md will be
  updated in the implementation task that introduces these shapes.

## Testing and Acceptance

Unit tests use a fake Telethon client and cover link parsing, output-path
sanitisation, no-overwrite behaviour, no-media and not-found errors, private
link resolution fallbacks, state/resume offset selection, and the translated
revoked-session error. Each behavior starts as a failing test.

The opt-in live suite will cover one accessible private link and an
interrupted-download resume fixture when safe credentials and media are
available. The phase acceptance run additionally downloads a video larger
than 100 MB to `~/Downloads`, records single-stream and parallel timings on
that same file, and exercises `t.me/c/3817664407/878`. A reproducible current
Telethon failure on that incident link is the only trigger for a separate
TDLib PoC under ADR-0009.
