## 2026-08-13 — Wave E housekeeping (Cursor)

**Did:** Marked thermos backlog T26–T37 / #272–#280 as on `main`; updated
`docs/ISSUES.md` Wave E wording. Deleted superseded remote topic branches
with no open PR (`cursor/*-1ec8`, `clone-lookup-kind-5c2c`,
`export-append-atomic-9070`, `media-download-resumable-a641`). Confirmed
`publish-thermos-backlog.py --apply` still fails without `issues:write`.

**Decided:** Leave GitHub Issues unpublished until an owner token can run
`--apply`; ticket bodies stay in-repo.

**Learned:** Squash-merged Wave E left duplicate `*-1ec8` remotes that
were not ancestors of `main` but carried no unique open work.

**Next:** none for this housekeeping slice.
