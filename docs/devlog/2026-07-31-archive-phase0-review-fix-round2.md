## 2026-07-31 — Archive Phase 0 review fix round 2 (Cursor)

**Did:** closed the major regression from the scoped re-review of
`799a671..09f451c`. Added CLI-seam regressions proving that a missing
explicit `--message-ids` entry becomes an additive `failed` row and the batch
continues — both without filters and with `--type`. Fixed
`_iter_bulk_candidates()` so candidate-resolution `NotFoundError` is yielded
as a per-item `resolve_error` instead of aborting the async generator before
`download_media_bulk()` can record `failed` and continue.

**Decided:** keep resolution in the candidate iterator (filters still need the
message object) but surface NotFound as a tagged yield rather than an
exception that escapes the per-item loop. No CONTRACT/ADR change — the
documented "Per-item NotFound goes into `failed` and continues" rule was
already correct.

**Learned:** mocking `resolve_message` in earlier bulk tests hid the generator
raise path; only unmocked FakeClient resolution exercises the real abort.

**Next:** re-approve Phase 0, then Phase 1 (store/scope/selected backfill).
