# tg doctor

`doctor` is a read-only environment and session health report. Reach for it
when a command is failing and you need to know whether the problem is local
(bad permissions, a stale lock, a missing session) or on Telegram's side.

## Check every configured account

Without `--account`, `doctor` walks every account in the config.

```bash
tg --json doctor
```

## Check one account

```bash
tg --json doctor --account main
```

## Offline by default

By default `doctor` never opens a Telegram client. It only checks local
state: config/session file presence, lock freeness, state directory
writability, preview, audit, and session file permissions, and total state
size.
`checks.authorized` is `null` — unknown, not `false` — because it was never
probed.

Offline-first is deliberate: the live probe fails in exactly the two
situations where you most need a diagnosis — a revoked session or a dead
network — so an unconditional live check would be least available when it
matters most. Local checks always run and always tell you something, even
with no network at all. For a single-account offline view (path, mtime,
lock, `.bak`) without walking every account, use
`tg accounts show ALIAS` instead.

## Probe live authorization

```bash
tg --json doctor --connect
```

| Flag | Effect |
| --- | --- |
| `--connect` | additionally call `get_me` to confirm the session is authorized; populates `checks.authorized` (bool) and `user` on success |

With `--connect`, any exception during the live probe — including a
session or configuration failure — is captured as `checks.error`, with
`authorized: false`, `user: null`, and `ok: false` for that account, rather
than raising.

## Checks reference

| Check | Meaning | If false |
| --- | --- | --- |
| `session_file` | session file exists for this account | run `tg accounts import` or sign in |
| `lock_free` | no other `tg` process holds this session's lock | wait for the other process to exit, or check for a stale lock |
| `state_writable` | the preview-state directory accepts writes | check permissions/ownership of `~/.local/state/tgcli/` |
| `preview_perms_ok` | no preview file is readable by the group or other users | doctor tightens loose files itself; only an unfixable file (or `--readonly`) leaves this false |
| `preview_perms_repaired` | how many preview files this run chmod'd back to `0600` (informational, not pass/fail) | — |
| `audit_perms_ok` | `audit.jsonl` is not readable by the group or other users | `chmod 0600 ~/.local/state/tgcli/audit.jsonl` |
| `session_perms_ok` | this account's `.session` file (and `.session.bak`, when present) are not readable by the group or other users; missing files pass | `chmod 0600 ~/.local/state/tgcli/sessions/NAME.session*` |
| `state_size` | total bytes under the state root (informational, not pass/fail) | inspect with `tg store stats` if unexpectedly large |
| `authorized` | (only under `--connect`) the session is live and accepted by Telegram | re-authenticate the account |
| `governor_cooldowns` | active per-request-type Telegram cooldowns from the request governor's ledger, with deadlines (informational; a cooldown is reportable state, not a failure) | see below |
| `governor_degraded` | the governor's ledger could not be opened; authenticated Telegram traffic refuses with exit 2 until repaired (ADR-0089); sets `ok: false` | fix or remove `~/.local/state/tgcli/governor.db` |

Per-account `ok` reflects only the local checks when `--connect` is absent;
with `--connect`, `ok` additionally requires `authorized: true`.

## Cooldowns

When a command refuses with exit 5 and `retry_after`, `tg doctor` is the one
command that still works: cooldowns are per request type, so a cooling
account is not one state. `checks.governor_cooldowns` lists each cooling
request type with its deadline; `governor_degraded: true` means the ledger
could not be opened — governed commands refuse with exit 2 (`BLOCKED`) and
doctor sets `ok: false` until the file is repaired (ADR-0089). The governor
probes each cooldown once at half the wait, so an early-lifted limit clears
itself without operator action.

## JSON

```json
{"runtime":{"python":"/home/me/tgcli/.venv/bin/python","python_version":"3.12.9","telethon":"1.44.0"},
"accounts":[{"alias":"main","session":"/home/me/.local/state/tgcli/sessions/main.session",
"checks":{"session_file":true,"lock_free":true,"state_writable":true,
"preview_perms_ok":true,"preview_perms_repaired":0,"audit_perms_ok":true,
"session_perms_ok":true,
"state_size":4096,"authorized":null,"governor_degraded":false,"governor_cooldowns":{}},
"user":null,"roles":[],"ok":true}],"ok":true}
```

`governor_cooldowns` maps each cooling request type to its deadline (an
empty object means nothing is cooling); `governor_degraded: true` means the
governor's ledger could not be opened and fails the account health check.
When cooldowns are active, `governor_cooldowns` looks like:

```json
{"messages.GetHistoryRequest":"2026-08-03T00:00:00+00:00"}
```

The top-level `runtime` object identifies the Python interpreter and Telethon
version used by this `tg` invocation. It is diagnostic only. If a helper script
needs to open a session, use `tg` or the checkout's `.venv/bin/python`, never
bare `python3`.

With `--connect`, `authorized` becomes a boolean and `user` is populated
(`id`, `username`, `name`) on success.

`--plain` uses frozen columns: `alias`, `status` (`ok`/`fail`/`unknown`),
`username`, `failures`. `unknown` means local checks passed and
authorization was not probed (no `--connect`).

## Notes

`doctor` itself always exits 0 — consult the top-level `ok` and per-account
`ok` fields for health results. The one exception: an invalid/unreadable
config file, or an explicitly named unknown `--account`, prevents the check
from running at all and keeps the normal config/auth exit 3.

## See also

- [safety](safety.md) — what `preview_perms_ok` and `audit_perms_ok` protect
- [../CONTRACT.md](../CONTRACT.md) — §5.1, the frozen JSON/`--plain` shapes
- [../decisions/ADR-0040-wacli-review-adoption-scope.md](../decisions/ADR-0040-wacli-review-adoption-scope.md)
