# ADR-0130: CLI contract, commands and output decisions

Status: Accepted

## Context

These decisions about the CLI entrypoint, commands, output, formatting, config, login state and the MCP/serve adapters used to live as code comments beside the
code they explain. ADR-0132 removes comments from code, so the non-obvious
ones move here, grouped by module and by the function, class or name they
sit on. Each entry is the reason for a choice that the code alone does not
show; the code is the authority for what it does.

## Decision

### `src/tgcli/atomic.py`

- `replace_text`: the CLI's signal escapes are BaseExceptions

### `src/tgcli/cli.py`

- `HELP_TOKENS`: The only argv tokens that may end a run successfully with text on stdout. argparse groups single-dash short options, so the value `-hi` also reaches the help action — that exit is misuse, not a help request.
- `_execute_network`: The audit role must cover every append_audit call this invocation makes, not just the network coroutine's client window: the before/after audits below run outside dispatch.run_network, so setting it only there (as dispatch used to) left the cli-level mutation audit rows without "role" (CONTRACT.md §6).
- `main`: Emitting is part of the invocation: a failure here is journaled, not reported as a success.
- `main`: parser.error() already wrote usage to stderr, but a --json caller is still owed exactly one document on stdout (CONTRACT §2).
- `main`: Codes are recorded before the write: a reader that hangs up mid- envelope must not replace the real failure in the journal.
- `main`: The whole-body alarm fired; the asyncio deadline is already translated by deadline.run (with its grace window).
- `main`: The row is owed before the process leaves; entrypoint turns this back into the signal death the caller asked for.
- `main`: Untranslated failure (network, RPC, bug): still one envelope, and a traceback only when the caller asked for diagnostics.
- `main`: An abnormal unwind (SIGINT) must not leave the journal claiming the pre-failure exit code with no error at all.

### `src/tgcli/commands/accounts.py`

- `_remove_config_block`: The scan is line based: a header-like line inside a multi-line string would cut the wrong text. Write only when exactly the alias is gone.
- `remove_account`: Role sessions are full-access keys of the same account: they go with it, since `--role` removal is unreachable once the config block is gone.
- `_remove_late_roles`: A login promoting this role holds its lock only briefly; failing here would leave a live key after the config block is gone.
- `_remove_role`: A case-sensitive filesystem can hold one logical role under several spellings; each is a live key, so every one of them goes.

### `src/tgcli/commands/doctor.py`

- `run`: Previews are account-agnostic, so the repair runs once for the whole invocation and every account report quotes the same number.

### `src/tgcli/commands/login.py`

- `_probe_if_needed`: Orphan session for a not-yet-configured alias: probe with the credentials the caller just supplied so we never overwrite a still- authorized key without --force.
- `_discard_if_account_removed`: an unreadable config proves nothing; keep the new session
- `_discard_if_account_removed`: Leave the pre-login state: a `--keep-session` remove kept the old session, which this promote had just moved to the backup.

### `src/tgcli/commands/media.py`

- `_resolve_private_entity`: A t.me/c/ link names a channel: ids are only unique within a peer kind, so a user with the same number is a different peer.
- `_message_filename`: Telethon names only documents with a filename attribute; photos and voice notes still have an extension from their type.
- `_resume_offset`: No state means no proven byte: discard the partial and start over rather than wedging every later run on an unresumable file.
- `_resume_offset`: Ordering matters and is the point of this block. The state file is keyed by source alone, so a re-run with a different `--output` lands on this same record: that is a confused invocation and has always been exit 2. The media-identity restart below is *quieter* than that error, so it is asked second — otherwise a source that also replaced its media would silently swallow the wrong-output diagnostic (review finding).
- `_resume_offset`: The source replaced the file behind this message. The partial bytes belong to the old one, and appending the new file's tail to them would publish a splice that passes every length check and is neither file (#180). Not operator error — say so and start over.
- `_download_target`: Only the default name changed; an explicit --output keeps its own check.
- `_download_target`: The final name means complete; a stream that ended early is not. The checkpoint above already made `current` durable, so the next run resumes from here instead of restarting (mirrored from clone reupload's `download_resumable` guard).

### `src/tgcli/commands/media_bulk.py`

- `download_media_bulk`: The id prefix keeps two media of this chat with one name apart (ids are per chat, so another chat sharing the directory can still collide).

### `src/tgcli/commands/mutate.py`

- `commit_edit`: edit_message resolves the peer internally, so an unresolvable chat surfaces here exactly as it does on the delete path.

### `src/tgcli/commands/run.py`

- `SIDE_EFFECT_GETTERS`: Getters with a side effect: pressing a bot button or querying an inline bot, publishing a location, counting a view, an ad impression.
- `PLUMBING`: Telethon exports this session's authorization to another datacentre to download media stored there. It is session plumbing, not a user write.
- `DENIED_NAMESPACES`: Credentials and account lifecycle stay with `tg accounts`: refused even to read, since auth.checkPassword and account.getTmpPassword only look like reads.
- `ROOT_ALLOWED`: The root namespace is MTProto session plumbing: `destroyAuthKey` and `destroySession` end the session like a logout, the rest is key exchange. Only the pings are let through; invoke wrappers are unwrapped before this.
- `WRITE_ARGUMENTS`: A getter that takes one of these acts on it: rotating a stream key (`revoke`), accepting or declining an offer (`decline`), a cloud-password check (`password`). It is a write whatever its name says.
- `run`: The script's own bug: its traceback is what the agent fixes it from.

### `src/tgcli/commands/search.py`

- `fetch_search`: One hit past the limit tells a full page from a complete answer.

### `src/tgcli/commands/send.py`

- `_video_info`: A zero `a` in the display matrix means a quarter turn.
- `_uploaded_media`: #285: without this attribute Telegram shows a video as a file row.
- `_uploaded_media`: Telegram streams only MP4-like containers.
- `_uploaded_media`: Unset, Telegram may turn a video without sound into a GIF.
- `commit_forward`: A retry after an unknown outcome skips the content check: the stored random_id lets Telegram answer already_sent instead of a second forward.

### `src/tgcli/config.py`

- `load_config`: The api_hash is a secret; a config made by hand is usually 0644.

### `src/tgcli/desktop.py`

- `_ask_osascript`: Answer arrives on stdout; the secret is never an argv element of ours beyond the AppleScript source that only contains the prompt text.

### `src/tgcli/dispatch.py`

- `run_network`: cli._execute already holds the audit-role context var for the whole invocation (including this coroutine); only the network session itself needs the role here.

### `src/tgcli/formatting.py`

- `SUPPORTED_HTML_TAGS`: Everything docs/CONTRACT.md documents for --format html. Anything else parses as markup and is silently deleted from the body, so it is an error: the operator approved a preview of the full text.
- `_HtmlParser.handle_data`: HTMLParser runs with convert_charrefs=True, so a charref is expanded after the input was surrogate encoded and arrives here as a real code point. Re-encode (idempotent for already-surrogated data) so entity offsets keep counting UTF-16 code units.
- `_parse_html`: feed() leaves an unfinished tag in rawdata and drops it from .text; never call close(), which flushes rawdata and hides the truncation.
- `_EMBEDDED_PHONE`: Free-form exception text can echo phones with display punctuation even when the CLI normalized its input. Require a leading `+` and at least four digits, but allow common separators between them; bare numeric ids stay untouched.

### `src/tgcli/handlers.py`

- `accounts_remove`: Gated here, after the config loads, not in preflight: a missing config has always been reported before the readonly refusal.

### `src/tgcli/login_state.py`

- `_write_attempt`: The attempt json holds alias/phone_code_hash/ created_at, never the phone. A regression here would put the raw phone back on disk for the whole 30-minute TTL (T08).
- `load_attempt`: A naive stamp (hand edit, older build) cannot be compared to an aware now: treat the attempt as unreadable rather than crashing a command with a TypeError.
- `load_attempt`: A read never deletes. A forward clock step during a multi-step login would otherwise destroy the staged session the user is still creating. Reaping belongs to `discard_attempt`.

### `src/tgcli/mcp/untrusted.py`

- `UNTRUSTED_KEYS`: Keys whose string values come from other Telegram users (CONTRACT §5).
- zero-width space/non-joiner, LRM, RLM
- bidi embeddings and overrides
- word joiner, invisible operators
- bidi isolates
- zero-width no-break space
- tag characters

### `src/tgcli/messages.py`

- `_custom_emoji`: Entities index into the raw text (.message), not the formatted .text.

### `src/tgcli/output.py`

- `_CONTROLS`: the CLI contract: human/plain output strips control characters from untrusted content. C0 (including tab and newline), DEL, and C1 all go — a name that smuggles its own tab or newline would also forge a TSV column or row, and the separators are the writer's to produce. --json keeps the data verbatim.
- `_JSON_CONTROL_ESCAPES`: --json keeps the data verbatim but escapes DEL and C1: json escapes only C0, and with ensure_ascii=False a raw CSI/OSC from a message would reach the terminal. Escaped, the parsed value is unchanged.
- `emit_json`: Encoded whole before the first write, so a late encoding error leaves stdout empty for the error envelope; escaped per chunk, so a large document is held once, not twice.
- `emit_json`: Flush here so a reader that hung up raises BrokenPipeError while cli.py can still handle it, not during interpreter shutdown.
- `emit_error`: The one boundary every error leaves through, so phones are masked here (the CLI contract), whichever layer wrote the message.
- `emit_error`: Same reason as emit_json: surface a hung-up reader to cli.py, not to interpreter shutdown.

### `src/tgcli/preflight.py`

- `_prepare_max_runtime`: --timeout is a hang detector; a non-positive deadline is meaningless and behaves differently for local vs network commands (review D2).

### `src/tgcli/registry.py`

- `_login_deadline`: `--continue` waits on a person typing a code: no implicit deadline.

### `src/tgcli/serve.py`

- `RESERVED_FLAGS`: The server fixes these; a request that sets them would reach another session.
- `invoke`: stdin is the request pipe: a command that reads stdin gets the request's text.
- `main`: Replies own the real stdout; whatever else a command prints is captured.
- `main`: The parent reads stdout for the ready line: a failed start answers there.

## Consequences

A change that breaks one of these reasons updates or removes its entry in the
same PR. A new decision of this kind is added here, not as a code comment.
