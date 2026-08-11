# Accounts

Managing configured accounts, authorizing sessions, choosing which account a
command runs against, and how account entries map onto session files and their
locks ([ADR-0042](../decisions/ADR-0042-accounts-login.md)).

## List configured accounts

```bash
tg --json accounts list
```

Reads `~/.config/tgcli/config.toml` only; opens no Telegram session.

## Show offline account status

```bash
tg --json accounts show main
```

Strictly offline: config presence, resolved session path, existence, size,
mtime, whether the account lock is currently held, and the `.bak` slot.
`authorized` is always `null` — use `tg doctor --connect` for a live probe.
Named session roles (ADR-0062) appear under `roles[]` with the same offline
fields. An unknown alias exits 4.

## Named session roles

One account can hold several independently authorized Telegram sessions so a
long job (`clone sync`, `export`, and similar long-held locks) does not block
every other command on the same account.

```bash
# Authorize a role beside an already-configured alias
tg --json accounts login main --role job --phone PHONE

# Use it for any command
tg --session-role job --account main --json dialogs
tg --session-role job --account main clone sync @source

# Inspect / retire
tg --json accounts show main
tg --json accounts remove main --role job --confirm
```

| Rule | Behavior |
| --- | --- |
| File | `sessions/<session>@<role>.session` with its own lock. |
| Names | Same charset as aliases; `primary` is reserved (omit `--session-role`). |
| Fallback | None. A missing role is exit 3 with `tg accounts login <alias> --role … --phone PHONE`. |
| Creation | Only via interactive `accounts login --role` — never implied by the flag. |
| Cost | Each role is one more device in Telegram Settings → Devices; revoke there when retiring. |

`--session-role` is a global flag (beside `--account`). Audit and invocation
journal rows record the role when set.

## Authorize a session: `accounts login`

Authorization starts only with an explicit phone number, then continues with
the confirmation code and, when required, the Telegram cloud password.

```bash
# New alias — provide api credentials once
tg --json accounts login tmp-login --phone +79991234589 \
  --api-id ID --api-hash HASH

# Re-authorize an existing alias (refuses if still authorized unless --force)
tg --json accounts login main --phone +79991234589 --force

# Continue the staged phone login
tg --json accounts login --continue LOGIN_ID --code 12345
tg --json accounts login --continue LOGIN_ID --password-stdin   # headless 2FA
```

| Flag | Effect |
| --- | --- |
| `ALIAS` | Account to authorize (omit with `--continue`). |
| `--phone PHONE` | Required phone number for a start invocation. |
| `--api-id` / `--api-hash` | Required together, only for an alias absent from config. |
| `--force` | Replace a still-authorized session (previous file kept as `.bak`). |
| `--timeout SECONDS` | Override the ordinary 60-second hang detector. |
| `--continue LOGIN_ID` | Resume a pending attempt (no `ALIAS`). |
| `--code VALUE\|-` | Confirmation code on `--continue` only; `-` reads one line from stdin. |
| `--password-stdin` | Read the cloud password on `--continue` (never argv). |
| `--role NAME` | Authorize a named session role beside a configured alias (ADR-0062). |

`--readonly` / `TGCLI_READONLY=1` block login. `TGCLI_NO_SEND` does **not**.
On macOS the cloud password and confirmation code are collected through a
native dialog when one is available. Headless environments must pass
`--code VALUE` or `--code -` for the phone confirmation step; for 2FA they
return `"next": "password"` at exit 0 and resume with `--continue` +
`--password-stdin`. Attempt state lives under `logins/` and is promoted into
`sessions/<alias>.session` only after Telegram confirms — a failed attempt
cannot damage a working session.

Telegram Settings → Devices shows tgcli connections as **tgcli** with the
installed tgcli version, rather than an architecture-only label such as
`arm64`. This makes forced reauthorization and device cleanup distinguishable
from Telegram Desktop and mobile clients.

## Remove an account

```bash
tg --json accounts remove work            # report-only, exit 2, hint --confirm
tg --json accounts remove work --confirm
tg --json accounts remove work --confirm --keep-session
tg --json accounts remove work --role job --confirm   # role only; config untouched
```

Deletes the `[accounts.<alias>]` block and, unless `--keep-session`, the
session file and its `.bak`. With `--role`, only that role's session/bak are
removed — never config, never the primary; `--keep-session` is rejected.
Refuses when the lock is held, when the alias is `default_account` (whole-
account remove only), or under `--readonly` with `--confirm`.

## Import sessions from the old stack

```bash
tg --json accounts import
```

A local-only command — it never opens a Telegram connection. With no
`ALIAS` arguments it tries the migration aliases `main`, `recklessou`, and
`teamsyncsage`; a missing old-stack source for one of these is reported as a
warning, not a failure. Naming an alias explicitly and finding no source for
it exits 4.

```bash
tg --json accounts import main --source-root /path/to/old/stack
tg --json accounts import main --force
```

| Flag | Effect |
| --- | --- |
| `ALIAS ...` | Which aliases to import (positional, repeatable). Default: the three migration aliases above. |
| `--source-root SOURCE_ROOT` | Root of the old-stack session tree, if not the default location. |
| `--force` | Overwrite an existing destination session; without it, an existing destination is left untouched. |

The command copies the old SQLite session file (with an online backup) into
`TGCLI_STATE_DIR/sessions`. A busy destination lock, or missing/unparseable
credentials for a newly configured alias, exits 3. Result JSON:

```json
{"imported": [{"alias": "pl", "session": "/home/me/.local/state/tgcli/sessions/pl.session",
               "status": "imported|skipped_existing|source_missing",
               "config": "added|unchanged"}]}
```

`--plain` emits `alias`, `status`, `config`. See
[CONTRACT.md §10](../CONTRACT.md) and
[ADR-0004](../decisions/ADR-0004-accounts-and-sessions.md) for the full
migration rationale.

## Selecting an account

Every command accepts `--account NAME`. When it's absent, resolution falls
back in this order:

1. `--account` flag
2. `TGCLI_ACCOUNT` environment variable
3. `default_account` in `config.toml`

If none of the three resolves to a configured alias, the command fails with
a config error (exit 3) before opening any session. This order lives in
`resolve_account` in `src/tgcli/config.py`.

```bash
tg --json --account work dialogs --limit 10
TGCLI_ACCOUNT=work tg --json dialogs --limit 10
```

## Sessions and locks

Each account's `session` key in `config.toml` names its session file:
`~/.local/state/tgcli/sessions/<session>.session` (default `TGCLI_STATE_DIR`
if overridden). This SQLite file holds both the Telethon auth key and the
entity cache that makes short-lived processes fast — it must never be opened
by two clients at once.

Open this file only through the `tg` entrypoint or `.venv/bin/python` from the
tgcli checkout. Do not use bare `python3` or a system/user-site Telethon:
Telethon versions can use incompatible SQLite session schemas. When debugging,
`tg --json doctor` reports the active interpreter and Telethon version in its
top-level `runtime` object.

To enforce that, every session open takes an exclusive, non-blocking
`flock` on a sibling `<session>.lock` file for the lifetime of the process.
A second `tg` process against the *same* account while one is already
running fails fast with **exit 3** and a message naming the busy session —
this is expected under concurrent use, not corruption, and is worth a short
retry rather than investigation. Parallel `tg` calls against *different*
accounts never contend: each has its own session file and lock.

```bash
tg --json --account main dialogs &
tg --json --account main read CHAT   # exit 3: session busy, retry in a few seconds
tg --json --account work dialogs     # unaffected, different account
```

## Multi-account usage

Configure as many `[accounts.<alias>]` blocks as you need in one
`config.toml`; pick one per invocation with `--account` (or `TGCLI_ACCOUNT`
for a whole shell session). There is no cross-account operation in a single
`tg` call — `clone`, `forward`, and every other command operate within one
resolved account's session.

## See also

- [install.md](install.md) — creating the config entries this page assumes.
- [safety.md](safety.md) — `--readonly` vs `TGCLI_NO_SEND` for login/remove.
- [store.md](store.md) — `logins/` attempt litter and `.bak` inventory.
- [doctor.md](doctor.md) — live authorization probe via `--connect`.
- [overview.md](overview.md) — exit codes, including the config/auth code 3 used for lock and resolution failures.
- [../decisions/ADR-0004-accounts-and-sessions.md](../decisions/ADR-0004-accounts-and-sessions.md) — why sessions and locks are shaped this way.
- [../decisions/ADR-0042-accounts-login.md](../decisions/ADR-0042-accounts-login.md) — login, show, remove.
- [../CONTRACT.md](../CONTRACT.md) — §10 for the accounts JSON/TSV contract.
