# ADR-0080: Release-notes extraction trims blank lines

Date: 2026-08-09
Status: accepted
Form: ADR-lite (ADR-0058)
Extends: [ADR-0077](ADR-0077-release-workflow-publishes-github-releases.md)'s
notes extraction in the `Release tag` workflow.

## Context

The ADR-0077 publish step extracts the bumped version's CHANGELOG section as
release notes. Two edge cases survived the initial review: a section whose
only content is the `prepare-release` marker leaves `"\n\n"` after the strip,
and `[ -z "$notes" ]` is false for a whitespace-only string — such a section
would publish a page entry with empty notes instead of failing red; and the
awk extraction starts the notes with the blank line after the `## [x.y.z]`
header, which showed up as a leading newline in the backfilled entries.

## Decision

The extraction pipeline strips the marker and any leading blank line
(`sed -e '/prepare-release:/d' -e '/./,$!d'`), and the missing-section check
tests the whitespace-free form (`tr -d '[:space:]'`). A marker-only section
now turns the run red exactly like a missing section.

## Contract impact

None: workflow behavior only; no CLI, JSON, or exit-code change. The red
failure mode was already the documented ADR-0077 behavior.
