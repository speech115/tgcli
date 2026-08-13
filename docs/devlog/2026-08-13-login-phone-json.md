## 2026-08-13 — T08: keep raw phone out of login attempt JSON (Cursor)
**Did:** fixed `src/tgcli/login_state.py` so `logins/l_*.json` never carries
`"phone"` (was persisted plaintext for the whole 30-minute TTL). After a
successful code request, the phone lives only in an atomic `0600`
`l_*.phone` sidecar, read by `--continue` and deleted by promotion, discard,
code expiry, or expired-attempt cleanup. `_write_attempt` rejects a phone key.
`commands/login.py::continue_login` reads the phone from `load_phone()`
instead of `attempt["phone"]`; `store stats` and `store cleanup` now inventory
and reap the sidecar with the attempt. Added regressions for inventory,
cleanup, mode, continuation, and failed-start retention. Full gate: 1911
passed, 9 skipped; ruff, format, architecture, pyright, coverage, and docs
green.
**Decided:** ADR-0093 governs the persistent sidecar schema. ADR-0042 §5
already excludes the phone from attempt JSON, but Telegram's sign-in request
requires the literal value across the process boundary; this is full-lane
state rather than a small-fix implementation detail. CONTRACT §§5/10 and MAP
now describe the file and its lifecycle.
**Learned:** the raw phone cannot be dropped outright — Telethon's
`SignInRequest` needs the literal phone string on the continue-login RPC,
and its phone-to-hash map is in-memory only (not in the SQLite session), so
a fresh `--continue` process can't recover it from the staged session alone.
**Next:** independent whole-diff Spec + Standards re-review before merge.
