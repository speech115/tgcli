## 2026-07-31 — Archive Phase 0 fidelity prerequisites (Codex)

**Did:** implemented the remaining Phase 0 archive prerequisites in the
existing read/export/media seams. `src/tgcli/commands/export.py` now passes
the resolved source entity into `message_to_dict`, so exported JSONL rows keep
real permalinks and preserve cross-chat reply peers instead of collapsing to a
bare id. `src/tgcli/commands/media.py` now distinguishes `video_note` as its
own media kind, exposes that kind through the manifest/download filter
surface, and treats bulk-download `output path already exists: …` as an
additive per-item `skipped` row rather than a batch-stopping policy failure.
Added regression coverage in `tests/test_cli_export.py`,
`tests/test_cli_media.py`, `tests/test_commands_media.py`, and updated the
batch expectation in `tests/test_cli_batch.py`; refreshed `docs/CONTRACT.md`
for the additive export/media behavior. Verification: focused suite
`tests/test_commands_read.py tests/test_cli_export.py tests/test_cli_export_incremental.py tests/test_commands_media.py tests/test_cli_media.py tests/test_cli_batch.py tests/test_read_ops.py`
passed (`139 passed`), then full `./scripts/gate.sh` passed
(`1554 passed, 9 skipped`).

**Decided:** no new ADR. This slice restores fidelity promised by existing
ADR-0068 prerequisites and keeps the public contract additive: export rows
reuse the existing read message shape more faithfully, `video_note` extends
the accepted media-kind enum, and bulk media adds `skipped` without changing
the frozen TSV shape.

**Learned:** the export fidelity gap was entirely in the caller, not in
`message_to_dict`: omitting `entity` simultaneously nullified permalink
generation and misclassified cross-chat replies as same-chat. Bulk media also
needed a distinct "already on disk" path; reusing the generic `failed` bucket
would keep archive reruns non-idempotent even though the data was already
present.

**Next:** land the remaining Phase 0 prerequisite slices, then let the
integrator assemble the archive-ready baseline before Phase 1 store work.
