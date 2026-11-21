# CLI Automation Contract

What scripts, launchd jobs and agents may rely on: how `tg` is invoked, what
goes to which stream, and what each exit code means. It describes `main`.

- Flags: `tg COMMAND --help`.
- JSON shapes: the golden files in [`tests/snapshots/`](../tests/snapshots/),
  checked by `tests/test_json_snapshots.py`. The fields launchd jobs and
  gbrain read stay stable; any other change is a `Breaking:` PR.
- Error classes: `src/tgcli/errors.py`, checked against the table in §4 by
  `tests/test_contract_exit_codes.py`.

## 1. Invocation

```
tg [global-flags] <command> [subcommand] [args] [options]
```

Commands include offline local-state helpers (`store`, `clone status`)
that need no config or Telegram session, plus the usual
account-scoped surface (`doctor`, reads, mutations, …).

Global flags (available on every command):

| Flag | Meaning |
|------|---------|
| `--account <alias>` | account alias from config; default: config `default_account` |
| `--session-role <name>` | named session role beside the primary; omit for primary |
| `--json` | machine output: one JSON document to stdout |
| `--plain` | stable TSV to stdout (no colors, no alignment) |
| `--readonly` | hard-block any mutating call in this invocation |
| `--timeout <sec>` | overall invocation deadline covering preflight and execution, in wall time (default 60; no default deadline for media, exports, `clone init\|sync`, or `archive backfill\|sync\|transcribe`, which own bounded work; `accounts login --continue` takes none) |
| `--max-runtime <sec>` | explicit wall-clock cap for long runs: exhausting it is a normal stop (exit 0), with `stop_reason` and a resume pointer where the command keeps a cursor |
| `-v/--verbose` | Python and Telethon debug diagnostics on stderr for this invocation |

Env equivalents: `TGCLI_ACCOUNT`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`.
Flag beats env, env beats config. `TGCLI_CONFIG` sets the config file path
(default `~/.config/tgcli/config.toml`) and `TGCLI_STATE_DIR` the state root
(default `~/.local/state/tgcli/`).

`TGCLI_READONLY` and `TGCLI_NO_SEND` are read case-insensitively with
surrounding whitespace ignored: `1`, `true`, `yes`, `on` enable the block;
unset, empty, `0`, `false`, `no`, `off` leave it off. Any other value fails
closed: a command that consults the variable exits 3 (`CONFIG`) with a message
naming the variable and the accepted values.

`--session-role NAME` resolves to `sessions/<session>@<NAME>.session` before
the client opens. Role names use the same charset/length rules as account
aliases; `primary` is reserved (omit the flag to use the default session).
There is **no implicit fallback**: a missing or unauthorized role is exit 3
(`CONFIG`) with remediation `run: tg accounts login <alias> --role NAME --phone PHONE`,
never a silent switch to the primary. Symmetrically, omitting the flag always
uses the primary even when roles exist. A role appears only through an
explicit interactive `accounts login --role` — never created by using the
flag.

## 2. Streams

- **stdout** — contract data only. With `--json`: exactly one JSON document.
  With `--plain`: TSV rows. Default (human) mode: readable tables/text.
- **stderr** — everything else: progress, hints, warnings, error messages.
  With `--json`, the final error is also mirrored to stderr as a single-line
  JSON object: `{"error": {"code": "FLOOD_WAIT", "message": "...", "retry_after": 42}}`.
  With `--json`, the error envelope is written to stdout as the run's single
  JSON document, then the identical line is copied to stderr as that last-line
  mirror — a `--json` caller may read either stream for the same object.
  It is the **last** line of stderr, not the whole stream: progress and
  warnings legitimately precede it. Every failure yields exactly one
  envelope — including an untranslated network or RPC failure, which is
  reported as `RUNTIME` rather than a traceback; the traceback appears only
  under `-v`.
- `clone sync` prints progress to stderr in every mode, including `--json`
 : `[sync <source_id>] <n>/~<total> · <activity>`, where `<n>` is
  messages copied into the destination so far (earlier runs included),
  `<total>` the best-effort source message count (`?` when unavailable), and
  `<activity>` a transport (`forwarded` / `reuploaded` / `snapshots`), a phase
  (`comments`, `roster`), or a ~5 MB transfer mark
  (`reupload · <file> · download|upload <done>/<size> MB (<pct>%)`). These
  lines are plain — no `\r`, cursor control, or color — and **informative,
  not contract data**: the shape may change without a version bump, agents
  must not parse it, and `2>/dev/null` silences it.

## 3. Stability Rules

- JSON: adding fields is allowed anytime. Renaming, removing, or retyping a
  field is a breaking change: the PR title starts with `Breaking:`.
- TSV: column order is frozen per command; new columns append at the end.
- Datetimes: ISO 8601 UTC (`2026-07-06T12:00:00+00:00`). IDs: as integers.
- Long options must be spelled in full. Option-prefix abbreviations (`--c`
  for `--confirm`, `--w` for `--write`) are not part of the contract and are
  rejected as unrecognized arguments.
- A value that begins with `-` is read as options, not as a positional: pass
  the flags first and such values after `--`
  (`tg send --preview --json -- @chat -hi`).

## 4. Exit Codes

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 0 | success | |
| 1 | runtime error | network (a lost connection ends `clone sync`/`init` and `archive sync` rather than degrading one item), unexpected exception |
| 2 | blocked by safety policy | `--readonly` + mutating command, `TGCLI_NO_SEND`, a `forward --preview` from a protected chat, a first `forward --commit` of a message edited since its preview |
| 3 | config/auth error | missing `--account` / `default_account`, dead session (any Telegram `UnauthorizedError`: revoked, expired, unregistered key, deactivated user — `needs reauthentication`), bad api_id, a session still busy after the wait below, an `archive sync`/`transcribe` already running for the account, a `media download` of the same message already running in another process |
| 4 | not found | unknown dialog, message id, media; unknown alias on `accounts show\|remove` (lookup) |
| 5 | rate limited | FloodWait longer than 30 s, or, in a mutation-safe session, a FloodWait longer than 30 s or a second one on the same send (`FLOOD_WAIT_X`, or `FLOOD_PREMIUM_WAIT_X`, the transfer speed limit of a non-Premium account: every "FloodWait" in this contract means either); `retry_after` in error JSON |

A command whose session another `tg` process holds waits up to **120 s** for
it, with one stderr note, then exits 3. The wait counts against `--timeout`
like any other time, so under the default deadline a session still busy
after 60 s ends the command as `TIMEOUT` (exit 1); it never outlasts
`--max-runtime`. Account administration (`accounts login|remove`) does not
wait.

**Floods.** A FloodWait of up to 30 s is slept out and the
request is sent again: the command succeeds, later. A longer one exits 5
with `retry_after`. `clone init --commit`, `clone sync` and `tg run --write`
open a mutation-safe session, which never retries a request blind, so
Telethon raises a FloodWait there at once. A FloodWait means Telegram did
not run the request, so the paced sends of `clone sync` (each `sendMessage`,
`sendMedia`, `sendMultiMedia` and `forwardMessages`) and the writes of a `tg
run --write` script sleep a flood of up to 30 s, within what `--max-runtime`
has left, and send the request once more. A longer flood, or a second one
on the resend, exits 5 with its own `retry_after`. Every other request in
those sessions, `clone init --commit` included, exits 5 on its first
FloodWait. tgcli keeps no flood state between invocations, with one
exception: a `clone sync` that ends in exit 5 writes the end of the wait into
that clone (`retry_not_before`, shown by `tg clone status`), and until it has passed the next `clone
sync` of that clone exits 5 at once with the remaining `retry_after` and sends
no request. For every other command, after exit 5 the caller waits
`retry_after` seconds before the next run, and a run started sooner sends
the request again and may draw a longer wait.

**Pacing.** A command that sends a handful of requests never
waits; a loop spaces its own. A read of one chat at the owner's request
(`read`, `search`, `media manifest`) leaves page spacing to
Telethon: no wait up to 3000 messages, 1 s a page beyond, so `read --limit
300` is three requests back to back. A history walk inside a loop waits 3 s
before each page after the first (100 messages a page): `archive backfill`
and `archive sync`, a bulk `media download` of a whole chat, and `clone
sync`. `export messages` reads inside a takeout session and is not spaced.
Past the first five in one run, each of these waits 3 s after the one
before it started:

- the dialogs of `archive backfill`, and the channels and catch-ups of
  `archive sync`;
- the pages of a dialog or participant listing in `archive backfill
  --private`, `clone init` and the `clone sync` roster;
- the files of a bulk `media download`, of the `archive sync` media pass and
  of a clone reupload;
- the sends of one `clone sync` run: `messages.sendMessage`, `sendMedia`,
  `sendMultiMedia` and `forwardMessages` share one slot, 20 a minute, and an
  album or a multi-id forward is one request;
- every write a `tg run --write` script sends.

These waits count against `--timeout` and `--max-runtime`.

Exit 1 covers several distinguishable error codes in the JSON envelope:
`TIMEOUT` (the `--timeout` deadline elapsed), `RUNTIME` (an untranslated
network or RPC failure), and `USAGE` (argument misuse detected after the
global flags parsed). A parse-time usage error still exits 1 with the usage
text on stderr and nothing on stdout, because argv was never parsed far
enough to know `--json` was asked for. A stdout pipe closed by the reader
(`tg … --json | head`) exits 0 and journals `BROKEN_PIPE`; a run killed by
SIGINT, SIGTERM, or SIGHUP journals `INTERRUPTED` / `TERMINATED` with
`exit_code` 128+signal and then dies by that signal, so the shell still sees
a signal death. SIGKILL cannot be caught and journals nothing.


For `read`, `search`, `message` and `info`, a negative numeric chat
reference is an exact marked peer id. After entity resolution, the command
verifies that the resolved peer has that same marked id before fetching
messages or returning peer data. A cache collision with another peer type
is refused as `NOT_FOUND` (exit 4), not read. Positive bare ids retain the
CLI's existing Telethon resolution; they are not type-specific.

## 5. Untrusted Content

Message texts, dialog names, and file names are untrusted input. In `--json`
mode they are passed through as data: non-ASCII text stays literal UTF-8, and
C0, DEL, and C1 control characters are written as `\u` escapes, so a parsed
value is unchanged but no raw control sequence reaches a terminal. In human
mode control characters are stripped. Phone numbers are masked in every
error message, whichever layer wrote it. tgcli never interpolates message
content into shell commands or file paths without sanitizing.

## 6. Invocation Journal and Diagnostics

Every successfully parsed command appends one JSON object to
`~/.local/state/tgcli/invocations.jsonl` (or `TGCLI_STATE_DIR/invocations.jsonl`):
`timestamp`, `command` (the full subcommand path, e.g. `archive sync`), resolved `account` when applicable, `role` when
`--session-role` was set (omitted for the primary), `exit_code`,
structured `error` code when applicable, and `duration_ms`. A failed run
also carries `error_site`, the `module:function` inside tgcli that raised it
(for example `session:session_file_lock` for a busy session), and an
untranslated failure carries `error_type`, the exception class name. A flood
exit (exit 5) carries `retry_after`. A normal stop
carries `stop_reason` (`wall_clock_cap`). The journal never contains message/search text, chat
references, request parameters, or command output. A journal-write failure
emits a warning to stderr but does not change the command result.

`-v` / `--verbose` enables Python and Telethon debug logs on stderr for the
current process. Stdout remains contract data in all output modes.

