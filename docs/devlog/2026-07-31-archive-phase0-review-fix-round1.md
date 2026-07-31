## 2026-07-31 — Archive Phase 0 review fix round 1 (Codex)

**Did:** addressed both findings from
`.superpowers/sdd/2026-07-31-archive-store/task-0-review.md`. Added a helper
regression in `tests/test_commands_media.py` proving filter-mode bulk
`--limit` counts successful downloads rather than skipped existing files, plus
two CLI-seam regressions in `tests/test_cli_media_bulk.py`: one for skip-only
JSON/exit-0 behavior and one proving `--limit` advances to later matching
candidates after earlier `output path already exists: …` skips. Fixed
`src/tgcli/commands/media.py` minimally by replacing eager filtered inventory
truncation with a lazy bulk-candidate iterator and by stopping on successful
downloads (`len(items)`), not attempted items. Focused verification passed:
`58 passed` across `tests/test_commands_media.py`,
`tests/test_cli_media_bulk.py`, and `tests/test_cli_media.py`; targeted red →
green regressions were run individually first.

**Decided:** no ADR and no contract-doc edit. The public JSON shape and TSV
columns stay unchanged; this round only corrects the already-documented
skip-existing semantics so `--limit` now matches the intended meaning at the
CLI seam.

**Learned:** the escaped regression lived in two places at once: helper-only
tests missed the CLI contract, and the original fix still used eager filtered
candidate selection (`manifest(..., limit=N)`), so the batch could stop before
it had produced `N` successful downloads.

**Next:** continue with the remaining archive-plan slices on top of this
verified Phase 0 baseline.
