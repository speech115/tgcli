## 2026-08-09 — restore the destroyed `tg spec` proposal entry

**Did:** PR #159's docs edit silently replaced the heading and first sentence
of the existing `tg spec` deferred-proposal entry (`**tg spec.** *Not a
wacli import* …`) with the new `tg doctor` (#96 option 4) paragraph, orphaning
the rest of the `tg spec` text under the wrong heading. The heading line is
restored verbatim from the merge-base (`14cc45d^`); the `tg doctor` paragraph
now stands alone and the provenance fact is back in the repo.

**Decided:** No further edits to either entry; the pre-merge Bugbot flag on
#159 was valid and should have blocked the merge.

**Learned:** A docs-only PR's diff deserves the same whole-diff review as
code: an automated reviewer is only useful if its note is resolved or waved
off before merge.

**Next:** none — this closes the review finding.
