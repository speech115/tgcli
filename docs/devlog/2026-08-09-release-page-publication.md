## 2026-08-09 — Release-page publication from the tag workflow (ADR-0077)

**Did:** Extended `.github/workflows/release-tag.yml` (ADR-0038's executor) to
publish the GitHub Releases page entry in the same run as the tag push: the
CHANGELOG section for the bumped version becomes the release notes, title is
`tgcli <version>`. The publish step is idempotent (`gh release view` guard), so
re-running the workflow repairs a run that died after tagging but before
publishing; a missing section or a failed publish turns the run red. Updated
`docs/agents/release.md` (fix-forward = re-run the workflow; confirm with `gh
release view`), the ADR index, MAP.md, and the AGENTS.md release line.

**Decided:** The Releases page had stalled at 1.2.18 because page entries were
never part of the automated flow — tags kept shipping (v2.0.0–v2.0.2), the page
did not. A tag-triggered companion workflow was rejected: GitHub does not fire
runs for tags pushed by `GITHUB_TOKEN`. Backfilled the three missing page
entries (v2.0.0–v2.0.2) by hand from their CHANGELOG sections. Found that
2.0.1 shipped with the `prepare-release` marker surviving in CHANGELOG
(the ADR-0074 "revisit" case; the 2.0.2 section never carried it) — the
workflow strips the marker from the notes so a marked section can never leak
into the page, and the 2.0.1 section was cleaned in place.

**Learned:** `gh release create` is session-safe (no tag push involved), so
the fix-forward path needs no tag access.

**Next:** the workflow publishes future releases automatically; watch the next
version bump to confirm the page moves on its own.
