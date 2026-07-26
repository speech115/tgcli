# ADR-0061: Per-run entity reuse in the clone comments leg (ADR-lite)

Date: 2026-07-26
Status: accepted

**Context.** The ADR-0051 interleave calls `comments.sync_phase` once per
50-batch window, and every call re-resolved the same two discussion peers
(`GetChannels` ×2) — ~36 redundant RPCs on an 18-window run, flagged as
repeat work by the 1.2.16 audit's performance lens. One `ResolveContext`
already spans the whole run.

**Decision.** `sync_phase` reuses `resolve_ctx.source_group` and the new
`resolve_ctx.destination_group` when set; only the first window fetches.
The `verify_tail` foreign-post guard stays per-window — it is a safety
check, not a resolve. A group that turns private mid-run is still caught
by the leg's own reads/sends through the existing degrade paths; only the
per-window re-resolve is skipped. Proven by a counting-fake regression:
three windows, exactly two `get_entity` calls.

**Rejected.** A general client-level RPC cache (wrapping the Telethon
client): touches the safety-critical session factory for no additional
measured repeat site — `attribution.py` and `quotes.py` already carry
their own per-run caches. Revisit only with `--profile`-style
whole-run RPC counting under the PROPOSALS performance-baseline work.

**Contract impact.** None: no flags, JSON, or exit codes change.
