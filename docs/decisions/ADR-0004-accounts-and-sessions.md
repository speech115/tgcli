# ADR-0004: Per-account SQLiteSession + file lock; import from old stack

Status: accepted (2026-07-06)

## Context
Telethon SQLiteSession files store the auth key AND the entity cache
(access_hashes) — that cache is what makes short-lived processes fast.
SQLite session files must not be opened by two clients concurrently.
The old stack solved this with one daemon per account (4 ports).
At acceptance, the migration scope contains authorized sessions for: main,
recklessou, teamsyncsage. `pl` was retired from the default migration list on
2026-07-10 after live verification showed its old session was unauthorized;
the old source is preserved and can be explicitly reauthorized and imported
later if needed.

## Decision
- Sessions live in `~/.local/state/tgcli/sessions/<name>.session`.
- Accounts registry in `~/.config/tgcli/config.toml`
  (`[accounts.<alias>]` → api_id, api_hash, session name; plus
  `default_account`). Selection: `--account` flag > `TGCLI_ACCOUNT` env >
  config default.
- Every client open takes an exclusive non-blocking `flock` on
  `<name>.session.lock`; a busy lock fails fast with exit 3 and a clear
  message instead of corrupting the session.
- `tg accounts import` (phase 6) copies authorized session files from
  `tools/telegram` — no re-login needed.
- api_id/api_hash stay in the config file (chmod 600). OS keyring is a
  possible later hardening, not v1 (YAGNI).

## Consequences
- Parallel `tg` calls on *different* accounts work; same account serializes.
- Session corruption class of bugs is prevented structurally.
