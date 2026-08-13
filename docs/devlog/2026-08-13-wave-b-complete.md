## 2026-08-13 — Wave B merge complete (Composer)

**Did:** Squash-merged Wave B onto `main` under owner OK despite
billing-red Actions (local gate as SoT, same posture as Wave A):

| PR | Topic | Notes |
|----|-------|-------|
| #244 | T20 authclient flood | no release |
| #245 | T16 journal flood fields | no release |
| #246 | T22 CONTRACT refresh | **3.0.5** |
| #248 | T08 login phone sidecar | **3.0.6**, ADR-0093 |
| #252 | T11 store cleanup lock | ADR-0094 |
| #256 | T17 runtime redaction | ADR-0095 |
| #247 | T24 archive busy_timeout | ADR-0096 |
| #255 | T09/T23 archive remove/scope | ADR-0097 |

Parallel tip ADR numbers 0110–0114 renumbered into 0093–0097 at merge.
Architecture ceilings ratcheted with #255.

**Decided:** Tip `f650bc4`; version **3.0.6**. Release-tag workflow may still
fail on billing — do not tag locally.

**Learned:** Merge-time ADR renumber + MAP/README/check_docs conflict
resolution is the hot path for parallel thermos tips.

**Next:** Remaining open thermos PRs / debt after owner direction.
