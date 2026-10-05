# ADR-0074: Complexity reset, and a script for the mechanical half of a release

Date: 2026-08-03
Status: accepted; rule 1 (complexity reset) superseded by ADR-0120
Form: ADR-lite (ADR-0058)
Extends: ADR-0058 rule 1, which already
holds ADR-0038's mechanics. Nothing about ownership moves — the integrator
still writes the version, `CHANGELOG.md`, and the tag; this ADR only gives
that job a tool. [ADR-0038](ADR-0038-versioned-releases-changelog.md) itself is
untouched.

## Context

Two rows from the 2026-08-03 process review in `docs/PROPOSALS.md`, adopted
after ADR-0073 landed the lanes. Both attack manual work rather than checks.

The release bookkeeping is mechanical and repeated: 32 releases in the
repository's first four weeks, each one a version moved in two files, a
`CHANGELOG.md` section opened with the right date, the ADR and PR list
assembled by hand, and the `[x.y.z]:` compare link the docs gate refuses a
release without. `check-docs.py` already validates that shape, which is the
evidence it is regular enough to generate. The parts that misfire are exactly
the mechanical ones: a version race between parallel branches (1.2.10/1.2.11),
`__version__` drift fixed in #51, a `CHANGELOG` finalized after tagging in #38.

The second row comes from `openai/openai-agents-python`, whose contract treats
a repeated review finding as a design signal: "Treat a second related review
finding that would add another condition ... as a mandatory complexity-reset
checkpoint, not another item to patch." This repo has the opposite default —
every confirmed defect gets a reproducing test and a minimal fix, which is
right per defect and wrong per pattern: three minimal fixes to the same
abstraction leave a worse abstraction with three tests pinning it in place.

## Decision

1. **Complexity reset.** A second related review finding that would add
   another condition, parameter, or compatibility mode to the same abstraction
   is a checkpoint: stop patching and reconsider where the boundary belongs.
   Unreleased code and its tests are not a sunk cost — ADR-0073 already fixes
   compatibility at the release tag. Lands as an `AGENTS.md` rule beside the
   reproducing-test rule it qualifies, not replaces.
2. **`scripts/prepare-release.py`** does the mechanical half: bumps the patch
   digit (or an explicit `--version`) in `pyproject.toml` and
   `src/tgcli/__init__.py`, inserts the dated `CHANGELOG.md` section above the
   previous one, lists the PRs and ADRs landed since the previous tag, and
   writes the compare link. It refuses a version that already has a section or
   would move backwards, and `--dry-run` prints without writing.
3. **The script does not write the release prose.** It leaves one marked line
   for what the release means to an operator, and the integrator replaces it.
   Generating that from commit subjects is how a changelog becomes a commit
   log; `jdx/mise`'s own rule for the same job is "write release notes for
   mise users, not a commit log."
4. **Integrator-only.** The script moves the version files, so running it on a
   feature branch would break ADR-0058's shared-file ownership. It belongs to
   the merge, like everything else it touches.
5. **It refuses a split state rather than compounding it.** The two version
   files are compared before anything is written; if they disagree — the exact
   state an interrupted run leaves — the script names both values and exits 1.
   Each file is replaced atomically through `tgcli.atomic.replace_text`. Cross
   file atomicity is not available, so the recovery path is a loud refusal, and
   it has its own test.

## Rejected alternatives

- **Generate the whole changelog from PR titles** (openclaw's model): this
  repo's entries carry contract references and operator consequences that no
  commit subject holds. Rejected for the prose, adopted for the bookkeeping.
- **Conventional commits as the input** (`jdx/mise`): would constrain every
  intermediate commit on a branch that gets squashed anyway. The squash
  subject already carries the PR number, which is all the script needs.
- **Tagging from the script**: the `Release tag` workflow already tags on push
  when `__version__` moves, and sessions cannot push tags.
- **Failing the gate when the marker line survives**: a release is not always
  the same commit as the section; the marker is a prompt, not a lock. Revisit
  if a marker ever reaches a tag.

## Contract impact

None. No CLI flag, JSON shape, or exit code changes — `tg` is untouched and
`docs/CONTRACT.md` needs no edit. `scripts/prepare-release.py` is developer
tooling; its own contract is the test file beside it.
