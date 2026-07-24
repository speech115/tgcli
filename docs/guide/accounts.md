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
An unknown alias exits 4.

## Authorize a session: `accounts login`

QR by default (no typed secret). Phone + confirmation code is the fallback.

```bash
# New alias — provide api credentials once
tg --json accounts login tmp-login --api-id ID --api-hash HASH

# Re-authorize an existing alias (refuses if still authorized unless --force)
tg --json accounts login main --force

# Bare token for an external QR renderer / phone camera
tg --json accounts login tmp-login --api-id ID --api-hash HASH --qr-format text

# Phone path
tg --json accounts login tmp-login --phone +79991234589 --api-id ID --api-hash HASH
tg --json accounts login --continue LOGIN_ID --code 12345
tg --json accounts login --continue LOGIN_ID --password-stdin   # headless 2FA
```

| Flag | Effect |
| --- | --- |
| `ALIAS` | Account to authorize (omit with `--continue`). |
| `--phone PHONE` | Use the phone + code path instead of QR. |
| `--api-id` / `--api-hash` | Required together, only for an alias absent from config. |
| `--force` | Replace a still-authorized session (previous file kept as `.bak`). |
| `--timeout SECONDS` | QR wait budget (default 120 when unset). |
| `--qr-format link\|text` | Deep link (default) or bare token payload. |
| `--continue LOGIN_ID` | Resume a pending attempt (no `ALIAS`). |
| `--code VALUE\|-` | Confirmation code, or `-` to read one line from stdin. |
| `--password-stdin` | Read the cloud password from stdin (never argv). |

`--readonly` / `TGCLI_READONLY=1` block login. `TGCLI_NO_SEND` does **not**.
On macOS the cloud password is collected through a native dialog when one is
available; headless environments return `"next": "password"` at exit 0 and
resume with `--continue` + `--password-stdin`. Attempt state lives under
`logins/` and is promoted into `sessions/<alias>.session` only after Telegram
confirms — a failed attempt cannot damage a working session.

## Remove an account

```bash
tg --json accounts remove work            # report-only, exit 2, hint --confirm
tg --json accounts remove work --confirm
tg --json accounts remove work --confirm --keep-session
```

Deletes the `[accounts.<alias>]` block and, unless `--keep-session`, the
session file and its `.bak`. Refuses when the lock is held, when the alias is
`default_account`, or under `--readonly` with `--confirm`.

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
