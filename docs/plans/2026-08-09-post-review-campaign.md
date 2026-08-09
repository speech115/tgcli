# Post-review campaign — fixes for the 2026-08-09 review findings

Date: 2026-08-09
Owner: sereja (request: "чини все" after the independent review)

## Scope

Fix every confirmed finding from the independent six-PR review
(2026-08-09). Six PRs, one per slice; release 2.0.3 carries the CONTRACT
changes.

| PR / branch | Slice | Lane | Release |
|---|---|---|---|
| `codex/post-review-fixes` (extended) | transcribe update matching, photo/text story handling, `--codec` scope (ADR-0078), parallel photo size, timeout `transcription_id`, CONTRACT §5 examples, MAP rows | full (ADR-0078) | 2.0.3 |
| `codex/transcribe-readonly-gate` | classify `transcribeAudio` as a mutation: `--readonly` gate + audit (ADR-0079) | full (safety) | 2.0.3 |
| `codex/api-allowlist-docs` | ADR-0010 body (41 methods), CONTRACT §6 count, guide/api.md access_hash note, converter boundary test | small (docs) | 2.0.3 (CONTRACT line) |
| `codex/proposals-tg-spec-restore` | restore the destroyed `tg spec` heading in PROPOSALS.md | small (docs) | — |
| `codex/archive-refresh-honest-docs` | re-scope guide/plist/devlog to the pre-dispatch reality; propose mid-run enforcement | small (docs) | — |
| `codex/release-workflow-pins` | pin the publish step in tests; workflow nits; devlog/ADR-0077 corrections | full (workflow; ADR-lite) | — |

## Order and conflict map

Sequential (shared files: CONTRACT.md, MAP.md, docs/decisions/README.md,
PROPOSALS.md). Branch 1 first; branches 4 and 5 both touch PROPOSALS.md and
must not run in parallel.

## Exit criteria

Each branch: full gate green, independent whole-diff review before merge.
Integration: merge the three CONTRACT-carrying branches, bump 2.0.3 via
`scripts/prepare-release.py` (CHANGELOG section naming ADR-0078/0079 with the
`[2.0.3]` compare link), tag, workflow publishes the release page.

## Out of scope (reported, not implemented)

- #157 option (a): real mid-run wall-clock enforcement for `archive refresh`
  (full lane, changes CONTRACT §13) — proposed in PROPOSALS.md instead.
- Repo-wide dialog-id convention (negative vs positive) — owner checkpoint.
