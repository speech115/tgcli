## 2026-08-13 — T08: keep raw phone out of login attempt JSON (Cursor)
**Did:** fixed `src/tgcli/login_state.py` so `logins/l_*.json` never carries
`"phone"` (was persisted plaintext for the whole 30-minute TTL). The phone
now lives in a sidecar `l_*.phone` file at the same 0600 mode, written by
`create_attempt`, read by the new `load_phone()`, and cleaned up by
`discard_attempt`/`promote` alongside the staged session. `_write_attempt`
now asserts `"phone"` is absent as a regression guard.
`commands/login.py::continue_login` reads the phone from `load_phone()`
instead of `attempt["phone"]` for both the `sign_in` RPC and the audit call.
**Decided:** no new ADR (small-fix lane). ADR-0042 §5 already specifies the
attempt json's fields as alias/`phone_code_hash`/`created_at` — never phone;
this restores that intent rather than deciding anything new. ADR-0088 and
CONTRACT §10 describe the masked *stdout* phone, which was already correct
and needed no wording change.
**Learned:** the raw phone cannot be dropped outright — Telethon's
`SignInRequest` needs the literal phone string on the continue-login RPC,
and `client._phone` is in-memory only (not in the SQLite session), so a
fresh `--continue` process can't recover it from the staged session alone.
**Next:** none; thermos T08 closed.
