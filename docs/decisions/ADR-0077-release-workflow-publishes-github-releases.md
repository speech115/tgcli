# ADR-0077: Publish the GitHub Releases page entry from the release-tag workflow

Date: 2026-08-09
Status: accepted
Form: ADR-lite (ADR-0058)
Extends: [ADR-0038](ADR-0038-versioned-releases-changelog.md) rule 3's
executor, the `Release tag` workflow. Ownership does not move — the workflow
stays the only publisher, sessions still never touch `refs/tags/*`.

## Context

The `Release tag` workflow creates the `vX.Y.Z` git tag but not the GitHub
Releases page entry. The 1.2.x page entries were manual `gh release create`
follow-ups; after 1.2.18 the page stopped moving while tags kept shipping
(v2.0.0–v2.0.2 all have tags and no page entry). A release whose notes live
only in `CHANGELOG.md` is invisible from the GitHub UI: the docs gate refuses
a release section without its compare link, so the section is guaranteed to
exist — but nothing ever lifts it onto the Releases page.

## Decision

The `Release tag` workflow publishes the GitHub Release in the same run,
immediately after the tag push. The CHANGELOG section for the bumped version
becomes the release notes (prose first, then the PR/ADR list); the title is
`tgcli <version>`. The publish step is idempotent: it skips when the Release
already exists, so re-running the workflow repairs a run that died after
tagging but before publishing — the tag branch already no-ops on an existing
tag, so a missing Release is the only failure left to fix. A missing CHANGELOG
section or a failed publish fails the run, the same no-silent-skip rule the
tag push already had.

## Rejected alternatives

- **A tag-triggered `on: push: tags` workflow**: GitHub does not start new
  runs for tags pushed by `GITHUB_TOKEN`, so it would never fire for the
  tags this workflow creates.
- **`prepare-release.py` publishing locally**: sessions cannot push tags, and
  a session-side publish is exactly the manual step this removes.

## Contract impact

None. No CLI flag, JSON shape, or exit code changes; `docs/CONTRACT.md` needs
no edit. The docs gate and the CHANGELOG compare-link rule are unchanged. The
two repository-config tests that pin the workflow keep passing — the trigger,
permission, and version-bump logic are untouched.
