## 2026-08-13 — T17 runtime redaction: mask phones in RUNTIME error envelopes (Cursor)

**Did:** Added `tgcli.formatting.mask_phones_in_text`, a `+<digits>` regex
scrub reusing `mask_phone` per match. Applied it at `cli.py`'s untranslated
`except Exception` path before wrapping into `TgcliError` (the RUNTIME
envelope on stdout/stderr for `output.emit_error`). Mirror-fixed the same
`{"code": "RUNTIME", "message": str(exc)}` shape in `commands/batch.py`'s
per-op failure branch and in `jobs/runner.py`'s `_error()` helper (feeds
`last_error` in persisted job state, read back by `jobs show`). Fixed two
`E501` lines in `scripts/publish-thermos-backlog.py`.

**Decided:** Small-fix lane (docs/thermos-audit-2026-08-13/tickets/T17).
No CONTRACT.md wording change — the existing §9 phone-masking sentence is
scoped to `accounts login` and stays untouched; RUNTIME masking is a gap
fix, not a new documented guarantee. `+<digits>` matches the CLI's only
phone-ref shape (CONTRACT §9, `chatref`/`identity._is_phone`), so bare
numeric ids (message/chat ids) are never touched.

**Learned:** `mask_phone` only redacts an already-known phone value; there
was no existing helper to scrub phone-shaped substrings out of free-form
exception text, so this added one rather than reusing something wider.

**Next:** none; T17 acceptance (phone masked in JSON/stderr RUNTIME
envelope, full gate green) is met.
