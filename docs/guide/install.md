# Install

How to get `tg` running: prerequisites, cloning and syncing, putting the
binary on your PATH, and creating the account config that every other
command needs.

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

## Clone and sync

```bash
git clone https://github.com/speech115/tgcli.git
cd tgcli
uv sync
uv run tg --help
```

`uv sync` creates `.venv/` and installs the pinned dependencies (including
`telethon`). `uv run tg --help` confirms the entrypoint works without
touching Telegram or reading your config.

Keep this runtime boundary when writing helper scripts: use `tg` or
`.venv/bin/python` from this checkout to open tgcli sessions. Never open a
`~/.local/state/tgcli/sessions/*.session` file with bare `python3`, because a
system/user-site Telethon may expect a different SQLite session schema.

## Put `tg` on your PATH

```bash
./scripts/install-link.sh
```

This symlinks `.venv/bin/tg` onto your PATH so you can run `tg` directly
instead of `uv run tg`.

| Variable | Effect |
| --- | --- |
| `TGCLI_BIN_DIR` | Directory to link into. Default `~/.local/bin`. |
| `TGCLI_REPO` | Repo root containing `.venv/bin/tg`. Default: the parent of the script's own directory (works when run in place from a clone). |

The script refuses to link if `.venv/bin/tg` is missing or not executable
(run `uv sync` first). After linking, it resolves `tg` on your current PATH
and — if that resolves somewhere other than the freshly linked path — prints
a warning to stderr so an old `tg` (or the retired `tools/telegram` wrapper)
doesn't silently shadow it. Make sure `TGCLI_BIN_DIR` precedes any conflicting
entry in your `PATH`.

## Configure an account

Create `~/.config/tgcli/config.toml` with credentials from
[my.telegram.org](https://my.telegram.org):

```toml
default_account = "main"

[accounts.main]
api_id = 123456
api_hash = "0123456789abcdef0123456789abcdef"
session = "main"
```

Schema, as read by the config loader:

| Key | Required | Meaning |
| --- | --- | --- |
| `default_account` | no | Alias used when `--account`/`TGCLI_ACCOUNT` are absent. |
| `accounts.<alias>.api_id` | yes | Telegram API id (integer) from my.telegram.org. |
| `accounts.<alias>.api_hash` | yes | Telegram API hash (string) from my.telegram.org. |
| `accounts.<alias>.session` | no | Session file base name under `~/.local/state/tgcli/sessions/`. Defaults to the alias itself. |
| `archive.root` | no | Override the archive store root (default `~/.local/state/tgcli/archive/`). See [archive.md](archive.md). |

A missing `api_id` or `api_hash` for a configured alias is a config error
(exit 3) the moment that alias is selected. See [accounts.md](accounts.md)
for how the `session` key maps to the actual session file and its lock.

Override the config path with `TGCLI_CONFIG` (default
`~/.config/tgcli/config.toml`) — useful for testing or for keeping multiple
independent configs.

## Verify

```bash
tg --json doctor
```

Offline health check: config/session file presence, lock freeness, state
directory writability, and preview/audit permission tightness — no network
call. Then confirm Telegram authorization itself:

```bash
tg --json doctor --connect
```

This opens a session and calls `get_me`; check the top-level `ok` and each
account's `checks.authorized`. See [CONTRACT.md §5.1](../CONTRACT.md) for the
full `doctor` JSON shape.

## Adopt an existing session

If you already have an authorized session from the old `tools/telegram`
stack, copy it in instead of logging in again:

```bash
tg --json accounts import
```

See [accounts.md](accounts.md) for the alias arguments, `--source-root`, and
`--force`.

## See also

- [overview.md](overview.md) — execution model, streams, exit codes.
- [quickstart.md](quickstart.md) — first commands to run once installed.
- [accounts.md](accounts.md) — account selection, sessions, and locks.
