## 2026-08-13 — Batch JSONL strict bool/int coercion (T15) (Cursor Agent)

**Did:** `read_ops.from_batch` gained `_batch_bool`/`_batch_int`/
`_batch_optional_int`; every `_SPECS` batch lambda's bool/int field
(`unread_only`, `all`, `full`, `global`, `replies`, `limit`, `before_id`,
`after_id`, `topic`, `message_id`, `context`, `depth`) now rejects any
non-conforming JSON type instead of `bool()`/`int()` truthiness. Fixed a
pre-existing `ruff` E501 pair in `scripts/publish-thermos-backlog.py`
while running the gate. Tests: parametrized coverage per field in
`tests/test_read_ops.py`, plus three end-to-end `tg batch` regressions in
`tests/test_cli_batch.py` for the ticket's motivating cases.

**Decided:** Full lane — this changes released `tg batch` behavior
(previously silent truthy-coercion or a `RUNTIME` crash, now `BLOCKED`
before that op's fetch), so ADR-0096 plus one `docs/CONTRACT.md` §5.0
paragraph naming the strict-type rule.

**Learned:** `read`'s `before_id`/`after_id`/`topic` had no int coercion
at all before this — the mirror-fix rule applies within one op, not just
across ops, when the fields share a type. The platform's session
checkpoint auto-committed this work mid-session to
`cursor/batch-jsonl-coercion-1ec8` before the ADR/CONTRACT/MAP follow-up
landed; both are the same slice, just two commits.

**Next:** none — T15 is done pending independent review.

**Review fix:** CONTRACT §5.0 now matches ADR-0096 — JSON `null` / absent bool and optional-int fields are unset, with regression tests.
