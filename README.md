<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme-banner.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/readme-banner-light.svg">
  <img alt="tgcli — stateless Telegram CLI for humans, scripts, and AI agents" src="docs/assets/readme-banner.svg">
</picture>

# ✈️ tgcli — Telegram CLI: read, search, send

[![CI](https://github.com/speech115/tgcli/actions/workflows/ci.yml/badge.svg)](https://github.com/speech115/tgcli/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A stateless Telegram client built on [`telethon`](https://github.com/LonamiWebs/Telethon). Signs in as your own user account over MTProto, does exactly one operation per invocation, and gives you JSON-first reading, search, media, export, and preview → commit correspondence from the command line — for humans, scripts, and AI agents alike.

> Third-party tool. Uses the MTProto user API via `telethon`. Not affiliated with Telegram. No background daemon: every `tg` command is a foreground process that exits, and `tg-serve` holds a session only while its caller runs.

## Features

- **Automation contract**: JSON on stdout, diagnostics on stderr, fixed exit codes ([docs/CONTRACT.md](docs/CONTRACT.md)). State lives only in `~/.config/tgcli/` and `~/.local/state/tgcli/`.
- **Read and search**: dialogs, reads by id or date, per-chat and global search, threads, context windows.
- **Safe writes**: `send`, `edit`, `delete` and `clone init` go through preview → commit with single-use ids and an audit log; a retried `send` commit never duplicates.
- **Media and export**: manifest, single and bulk downloads (resumable, striped), JSONL export with `--resume`.
- **Clone**: copy channels, groups, forums and private dialogs into owned destinations, with comments, pins and protected-content reupload.
- **Archive**: a per-account SQLite store kept current by `archive sync`, with local voice transcription and offline search.
- **Scripts**: `tg run SCRIPT` for anything without a command; read-only unless `--write`, refused entirely in readonly mode.

## Install

`tgcli` requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

Use `tg` (or this checkout's `.venv/bin/python`) for any code that opens a
tgcli session. Do not open `~/.local/state/tgcli/sessions/*.session` with bare
`python3`: a system/user-site Telethon may use an incompatible SQLite session
schema. `tg --json doctor` reports the active runtime under `runtime`.

```bash
git clone https://github.com/speech115/tgcli.git
cd tgcli
uv sync
uv run tg --help
```

On Apple Silicon or Linux, syncing with the `fast` extra also installs `cryptg`, native AES for Telethon that makes large media transfers much faster; `tg doctor` reports the backend in use as `runtime.aes`.

### Put `tg` on your PATH

```bash
./scripts/install-link.sh          # symlinks ~/.local/bin/tg -> .venv/bin/tg
```

Override the target with `TGCLI_BIN_DIR=/somewhere/bin`. The script warns if another `tg` already shadows it on your PATH.

### Configure an account

Create `~/.config/tgcli/config.toml` with credentials from [my.telegram.org](https://my.telegram.org). It holds your API hash, so create it private:

```bash
mkdir -p -m 700 ~/.config/tgcli
install -m 600 /dev/null ~/.config/tgcli/config.toml
```

Then fill it in:

```toml
default_account = "main"

[accounts.main]
api_id = 123456
api_hash = "0123456789abcdef0123456789abcdef"
session = "main"          # session file name under ~/.local/state/tgcli/
```

Then verify:

```bash
tg --json doctor --connect
```

## Quick start

```bash
# 1. What is waiting for me
tg --json dialogs --unread-only

# 2. Read and search
tg --json read @channel --limit 20
tg --json search --all "invoice" --limit 20

# 3. Send — two steps, always
tg --json send @user "hello" --preview     # returns preview_id
tg --json send --commit p_9f3a             # nothing leaves without this

# 4. Media and export
tg --json media download https://t.me/channel/42
tg --json export messages @channel --output messages.jsonl --resume

# 5. Health
tg --json doctor
```

Chat references accept `@username`, a `t.me/` link, or a numeric dialog id. `--json` is contract data, `--plain` is TSV; anything else is for humans and may change.

## Documentation

| Document | Job |
| --- | --- |
| [SKILL.md](SKILL.md) | every task mapped to its command, plus recipes for paging, catch-up, and retrying a send |
| [docs/CONTRACT.md](docs/CONTRACT.md) | invocation, streams, exit codes, floods and the journal |
| `tg COMMAND --help` | every flag of one command |
| [Operations](#operations) | running it on a schedule, shipping `main`, what to do when it breaks |
| [AGENTS.md](AGENTS.md) | the rules for changing this repository |

## Configuration

Config lives at `~/.config/tgcli/config.toml`:

| Key | Required | Meaning |
| --- | --- | --- |
| `default_account` | no | Alias used when `--account` and `TGCLI_ACCOUNT` are absent. |
| `accounts.<alias>.api_id` | yes | Telegram API id (integer) from my.telegram.org. |
| `accounts.<alias>.api_hash` | yes | Telegram API hash (string) from my.telegram.org. |
| `accounts.<alias>.session` | no | Session file base name under `~/.local/state/tgcli/sessions/`. Defaults to the alias. |
| `archive.root` | no | Archive store root. Defaults to `~/.local/state/tgcli/archive/`. |

Sessions, locks, previews, the audit log, and cache live under `~/.local/state/tgcli/` (mode `0700`). Account selection order is `--account` > `TGCLI_ACCOUNT` > `default_account`.

Global flags, environment variables (`TGCLI_READONLY`, `TGCLI_NO_SEND`, `TGCLI_CONFIG`, `TGCLI_STATE_DIR`, `TGCLI_ACCOUNT`) and exit codes are in [docs/CONTRACT.md](docs/CONTRACT.md).

## Preview → commit

Reads are free. `send`, `edit`, `delete` and `clone init` take two invocations: `--preview` renders the exact intent into a single-use record (5-minute TTL), and `--commit ID` sends that record without retyping it.

```bash
tg --json send @channel "<b>bold</b>" --format html --preview
# → {"preview_id": "p_9f3a", "to": {…}, "text": "…", "expires_at": "…"}
tg --json send --commit p_9f3a
```

- After a failed commit, retry the same preview id; never make a second preview. A `send` carries its stored `random_id`, so the retry confirms the first dispatch instead of duplicating it.
- `--readonly`, `TGCLI_READONLY=1` and `TGCLI_NO_SEND=1` block mutations with exit 2 before any request. `clone sync` and `tg run --write` mint no preview but are gated the same way.
- Every committed mutation is in the audit log.

## Operations

How the owner's Mac runs tgcli, how a merged change reaches it, and what to do
when something breaks. The exit codes and journal are defined in
[docs/CONTRACT.md](docs/CONTRACT.md), which wins where the two differ.

### Layout on the Mac

| Path | What it is |
|---|---|
| `~/Projects/tools/tgcli` | development checkout, where agents work |
| `~/Projects/tools/tgcli-prod` | git worktree of the same repository on branch `main`; this is what runs |
| `~/.local/bin/tg` | symlink to `tgcli-prod/.venv/bin/tg` (made by `scripts/install-link.sh`) |
| `~/.config/tgcli/` | config: accounts, `api_id` |
| `~/.local/state/tgcli/` | sessions, previews, audit, journal, clone and archive state |
| phone `~/apps/tgcli-next` | a separate checkout on the OnePlus that the remote tg-mcp connector runs through `tg-serve`; updated by hand |

Scheduled work (archive sync, transcription, clone sync) is launchd calling
the same `tg` on PATH; see the archive row in [SKILL.md](SKILL.md) and
[Keeping clones current](#keeping-clones-current).

### Keeping clones current

One launchd job per account runs `clone sync --all` on a short interval. It
checks every clone with one `GetPeerDialogs` per 100 peers, copies what is
new, treats a FloodWait as the account's, and exits.

```xml
<key>Label</key>
<string>com.tgcli.clone-sync.A</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/you/.local/bin/tg</string>
  <string>--account</string><string>A</string>
  <string>--session-role</string><string>job</string>
  <string>--max-runtime</string><string>240</string>
  <string>--json</string>
  <string>clone</string><string>sync</string><string>--all</string>
</array>
<key>StartInterval</key>
<integer>120</integer>
```

With `--session-role job` (authorize once: `tg accounts login A --role job
--phone PHONE`) a sync never holds the session your own commands need.

- An idle run costs one `GetPeerDialogs` and reports `"up_to_date":true`.
- A FloodWait that ends a sync is stored as `retry_not_before`; until then
  every run exits 5 without sending a request.
- `--max-runtime 240` ends a long copy as a normal stop (`remaining: true`);
  the next run resumes. The clone's lock refuses an overlapping manual run.

### Shipping a merged change

`main` is what runs, but a merge does nothing on the Mac until `tgcli-prod`
moves. Ship only when CI on `main` is green and no `clone sync` or `archive
sync` is running (`pgrep -fl 'bin/tg '`):

```bash
cd ~/Projects/tools/tgcli-prod
git rev-parse --short HEAD          # note it for a rollback
git fetch origin main && git merge --ff-only origin/main
uv sync --frozen --extra fast       # frozen lockfile; the extra keeps cryptg
```

If the merge refuses, someone committed in `tgcli-prod` by hand: read
`git log origin/main..HEAD` first. Then verify: `tg --version` matches
`pyproject.toml`, `tg --json doctor --connect` is `"ok": true` for every account
(with `runtime.aes` `cryptg`), `tg --json dialogs --limit 3` exits 0, and after
a clone or archive change the next scheduled run exits 0 in the journal.

To roll back: `git reset --keep <noted commit>` and `uv sync --frozen --extra
fast` in `tgcli-prod`, then fix forward on `main`. A change that migrated a
SQLite schema cannot be rolled back this way; the fix goes forward.

### Release (only when the owner asks)

Bump `version` in `pyproject.toml` and `__version__` in `src/tgcli/__init__.py`
in one PR, tag the merge commit `vX.Y.Z`, run `gh release create vX.Y.Z
--generate-notes`, then ship as above.

### When something breaks

Start with the exit code and the JSON error envelope on stdout
(`error.code` and `error.message`), then the journal:

```bash
tail -n 20 ~/.local/state/tgcli/invocations.jsonl
```

Each line has `command`, `exit_code`, `error` (the code only, such as
`CONFIG`), `error_site` (where in tgcli it failed) and, for exit 5, `retry_after`. It never holds
message text. Rerun the failing command with `-v` for Telethon debug logs on
stderr.

| Symptom | What it means | What to do |
|---|---|---|
| exit 3, `error.message` says `needs reauthentication` | the session is dead: revoked, expired, or the key is unregistered | `tg --json accounts login ALIAS --phone PHONE`, then `--continue LOGIN_ID --code …`; check with `tg --json accounts show ALIAS`. A role session (`--session-role job`) needs its own login with `--role job` |
| exit 3 after a wait (up to 120 s, less if `--max-runtime` is shorter), `error_site` `session:session_file_lock` | another tg run holds this session. `accounts login` and `accounts remove` do not wait: they exit 2 at once | wait for it, or find it with `pgrep -fl 'bin/tg '`. Scheduled jobs that overlap with manual work should use `--session-role job` |
| exit 3 from `archive sync` or `transcribe` | the same command is already running for this account (each has its own lock) | nothing; the next scheduled run continues |
| exit 5 with `retry_after` | Telegram FloodWait longer than 30 s, or any FloodWait in `clone init --commit`, `clone sync` or `tg run --write`. Nothing is stored: the wait is only in this error and in the journal row | wait `retry_after` seconds before the next run; an earlier rerun sends the request again and may draw a longer wait. A scheduler should skip its next start until then |
| exit 1 `TIMEOUT` with a `session … is busy; waiting` note on stderr | another tg run held the session for longer than `--timeout` (60 s by default) | wait for it, or pass a larger `--timeout`; the busy wait itself gives up after 120 s with exit 3 |
| `clone sync` or `archive sync` exits 1 mid-run | lost connection or an untranslated server error; the run stops rather than marking items failed | rerun the same command; both resume from their saved cursor |
| a run exits 0 with `stop_reason: "wall_clock_cap"` or `remaining: true` | it hit `--max-runtime` or a budget, not an error | nothing; the next run continues from the resume pointer |
| `archive status` lists `gaps` | Telegram could not deliver part of the update history (`differenceTooLong`), or the stored cursor did not decode | nothing; `archive sync` re-reads every chat that moved and clears the gap once no catch-up is left. Edits and deletions inside the lost stretch stay lost |
| `clone init --commit` stops partway (a flood on the discussion link, say) | the preview is spent only on success | rerun the same `--commit PREVIEW_ID` while it is unexpired (5 minutes); it resumes |
| exit 2, `BLOCKED` | a safety gate refused (`--readonly`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`), or an argument was refused (a bad `--limit`), or a busy session on `accounts login`/`remove` | for the gates: intended in agent sessions. For an owner-approved write, prefix that one command with `TGCLI_READONLY=0`; `TGCLI_NO_SEND=1` must be unset too |
| `doctor` shows `runtime.aes: "pyaes"` | slow pure-Python crypto | `uv sync --frozen --extra fast` in `tgcli-prod` installs `cryptg` |

If none of these fits, keep the journal line and the stderr output, and open
an issue. Never open a `.session` file with bare `python3` to look inside: the
pinned Telethon writes a schema that older versions crash on.

### tg-serve protocol

`tg-serve [--account main] [--session-role NAME]` keeps one session connected
for a program that runs many `tg` commands (the phone's tg-mcp uses it). Give
it its own session role: the held session stays locked while it runs.

It speaks one JSON object per line. After connecting it writes
`{"ready": true, "account": "main", "role": "mcp"}`. Each request is
`{"argv": ["read", "--limit", "5", "--", "@channel"], "stdin": ""}`: a `tg`
command without global flags (the server adds `--account`, `--session-role`
and `--json`; put positionals after `--`). Each reply is
`{"exit_code": 0, "stdout": "…", "stderr": ""}`, exactly what that `tg`
invocation would print.

- `stdin` is optional, for `run -`, `--code -` or `--password-stdin`.
- A request that sets `--account` or `--session-role`, or has no non-empty
  `argv` list, gets exit code 1 and a `USAGE` error.
- If the session cannot be opened, the server writes the error envelope
  instead of the ready line and exits with its code.
- Requests run one at a time; closing stdin ends the server.

## Status and contributing

In production use by its owner. New behavior starts from an owner request, and a bug fix starts from a reproducing test. Run `./scripts/gate.sh` before a pull request; CI runs the same steps ([.github/workflows/ci.yml](.github/workflows/ci.yml)). [AGENTS.md](AGENTS.md) holds the working rules. Report a security or privacy issue privately via [SECURITY.md](SECURITY.md); never paste session material or phone numbers into an issue.

## Credits

CLI posture from [`gogcli`](https://github.com/openclaw/gogcli); built on [`telethon`](https://github.com/LonamiWebs/Telethon).

## License

[MIT](LICENSE) © speech115. `telethon` ships under its own MIT license; this tool is not affiliated with Telegram.
