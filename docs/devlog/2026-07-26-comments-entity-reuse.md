# 2026-07-26 — ADR-0061 comments-leg entity reuse (Claude Fable 5)

**Did:** removed the audit-flagged repeat work in the interleaved sync:
`comments.sync_phase` re-resolved the same two discussion peers on every
50-batch window. It now reuses the run's `ResolveContext`
(`source_group` + new `destination_group` field); `verify_tail` stays
per-window because it is the foreign-post safety guard, not a resolve.
Red-first: a counting-fake regression (three windows → exactly two
`get_entity` calls) failed at 6 before the change. Full suite 1432
passed. ADR-0061 is the first use of the ADR-0058 ADR-lite form.

**Decided:** no client-level RPC cache — the remaining measured repeat
sites already carry per-run caches (`attribution`, `quotes`); a client
wrapper would touch the safety-critical session factory for no proven
win. Recorded in ADR-0061's Rejected.

**Learned:** the degrade path for a mid-run privatized group never
depended on the per-window re-resolve — the leg's own reads and sends
hit the same error classes; the re-resolve was only the *first* detector,
not the only one.

**Next:** --profile / whole-run RPC counting stays with the PROPOSALS
performance-baseline block (needs the owner's live baseline run).
