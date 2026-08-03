## 2026-08-03 — Complexity reset, and the release bookkeeping script (Claude)

**Did:** landed the two cheapest rows from the 2026-08-03 process review as
ADR-0074 (ADR-lite). First slice run under the ADR-0073 lanes: it edits
`AGENTS.md`, so trigger 7 puts it in the full lane — ADR, index row, review.

**Complexity reset.** A second related review finding that would add another
condition or compatibility mode to the same abstraction is now a checkpoint,
not another patch; unreleased code and its tests are not a sunk cost. Sits
beside the reproducing-test rule it qualifies, in the review workflow.

**`scripts/prepare-release.py`.** Bumps the patch digit (or `--version`) in
`pyproject.toml` and `src/tgcli/__init__.py`, opens the dated `CHANGELOG.md`
section above the previous one, lists PRs and ADRs landed since the previous
tag, and writes the `[x.y.z]:` compare link the docs gate demands. Refuses a
version that already has a section or moves backwards; `--dry-run` prints
without writing. It deliberately does not write the release prose — one marked
line stays for the integrator. Integrator-only: it moves the version files,
which a feature branch must not touch (ADR-0058).

**Evidence:** tests first (12 cases, red before the script existed), one of
them asserting the produced changelog satisfies `check_docs.release_problems`
— the gate's own rule, not a restatement of it. `./scripts/gate.sh` green:
1738 passed, 9 skipped. Dry run on this repo emits the 2.0.1 section naming
ADR-0073, #154, and #153, with the correct compare link.

**Review found three real defects.** The two version files were written in
sequence with no cross-check, so an interrupted run left `pyproject.toml`
ahead of `__init__.py` and a rerun would silently skip a version — the exact
incident class (#51 drift, the 1.2.10/1.2.11 race) the script exists to
prevent. The version regex matched any `version = "..."` line, not the
`[project]` one. And the "already has a section" test never reached that
branch: the backwards check fired first, leaving the recovery path untested.
Fixed with tests first: the files are compared before anything is written and
a split state is refused by name, each file is replaced through
`tgcli.atomic.replace_text`, the regex is anchored to `[project]`, and a
missing previous tag now says so on stderr instead of passing as an empty
slice. 16 tests, gate green.

**Next:** the CI aggregator is only worth adding together with enabling
required status checks on the repository — neither is done. Generating the CLI
reference from `build_parser()` still needs its own scope decision about which
half of `docs/CONTRACT.md` is derivable.
