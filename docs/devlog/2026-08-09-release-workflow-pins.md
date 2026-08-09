## 2026-08-09 — release-workflow publish pins and notes trimming (ADR-0080)

**Did:** The review of #162 found the ADR-0077 publish step (the point of the
slice) had no test coverage while every other invariant of the workflow is
pinned. Added string pins for the `gh release view` guard, the
`gh release create` call, the fail-closed `refusing a silent release`, and
the marker strip; fixed two extraction edge cases (leading blank line, and a
whitespace-only section passing the empty check) — ADR-0080, ADR-lite.
Corrected the devlog/workflow-comment factual error (only 2.0.1 shipped with
the marker) and recorded the ADR-0077 owner-request provenance (ADR-in-PR
route).

**Decided:** Shipped CHANGELOG sections stay untouched going forward — the
publish-time strip is the only marker removal.

**Learned:** An enforcement mechanism whose critical lines are unpinned is
one edit away from the exact silent regression it exists to prevent; the
string-pin pattern in test_repository_config.py is the cheapest guard.

**Next:** independent whole-diff review; merge order with the other
post-review branches.
