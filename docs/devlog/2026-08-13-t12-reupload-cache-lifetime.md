## 2026-08-13 — T12: reupload cache clears after confirm+save (Cursor)
**Did:** `_reupload_batch` rmtree'd the per-clone reupload media cache right
after Telegram accepted the send, before `_forward_batch`'s
`confirmed_destination_ids` + `state.save`. An unconfirmed send or a crash
before the save lost the downloaded bytes despite ADR-0052's cache. Moved the
`rmtree` into `_forward_batch`, gated on `mode == "reuploaded"`, run once
right after `state.save` succeeds. Added a reproducing test (unmatched
`random_id` in the send response) proving the cache and downloaded bytes
survive an unconfirmed send; existing success/flood-before-download tests
stay green.
**Decided:** full lane (mutation-path safety, ADR-0052 intent) — ADR-0102
amends ADR-0052 decision 7.
**Next:** none; ticket closed.
