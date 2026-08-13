## 2026-08-13 — Private backfill enum cursor (T37)
**Did:** Archive schema v8 adds `account_sync.private_enum_json`. Private
`--private` quanta resume `iter_dialogs` via ADR-0118 token so a second
quantum does not re-walk the completed head. Helpers in
`archive/private_enum.py`; regression in `tests/test_archive_jobs.py`.
**Decided:** ADR-0118 — durable GetDialogs offset on account_sync; pending
refs advance only after `more=false`. No CONTRACT surface.
**Learned:** Sibling `iter_dialogs` callers (dialogs/media/clone init) are
one-shot listings, not multi-quantum walks — no mirror port.
**Next:** Independent whole-diff Spec+Standards review before merge.

**Review fix:** freeze enum cursor once a pending incomplete is queued;
route persist via `write_account_sync`; regression for interleaved completed
dialogs under `max_dialogs=2`.
