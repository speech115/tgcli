# Accounts

Managing configured accounts, choosing which one a command runs against, and
how account entries map onto session files and their locks.

## List configured accounts

```bash
tg --json accounts list
```

Reads `~/.config/tgcli/config.toml` only; opens no Telegram session.

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
- [overview.md](overview.md) — exit codes, including the config/auth code 3 used for lock and resolution failures.
- [../decisions/ADR-0004-accounts-and-sessions.md](../decisions/ADR-0004-accounts-and-sessions.md) — why sessions and locks are shaped this way.
- [../CONTRACT.md](../CONTRACT.md) — §10 for the `accounts import` JSON/TSV contract.
