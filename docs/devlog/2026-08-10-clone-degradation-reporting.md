## 2026-08-10 — clone degradation survives a later flood

**Did:** landed ADR-0086 (#184). `clone sync` now prints one stderr warning
for every unsupported source message after its cursor checkpoint and for every
planted quote fallback after its copy checkpoint. The existing
`skipped_unsupported` / `quote_flattened` JSON, plain counts, clean-run exit 2,
and later FloodWait exit 5 stay unchanged.

**Decided:** fix the reporting lifetime, not add an acknowledgement subsystem.
Persistent unacknowledged fallbacks would need a state schema, replay rule, and
new operator action; stopping on the first fallback would change sync
throughput. The immediate per-message warning follows ADR-0085's reviewed
keyboard precedent.

**Learned:** both regressions reproduce only when the permanent degradation is
followed by a later flood. In the red runs the clone cursor and mapping had
advanced, stdout contained only the FloodWait envelope, and stderr contained
no degradation report. That distinguishes the defect from a tail-format test.

**Next:** independent whole-diff review, then the patch release.
