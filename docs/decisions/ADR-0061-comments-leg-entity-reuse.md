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
check, not a resolve. Skipping the re-resolve moves the place where a
mid-run privatized source surfaces: independent review disproved the
first draft's claim that the leg's reads already degraded — they
escaped as a raw exit 1. The degrade guard therefore moves with the
detection point: the window's source iterator carries the same
`(ValueError, RPCError)` → `comments: "unavailable"` transition as the
first-window resolve, FloodWait still re-raised, `copy_batch` failures
still escaping unchanged. Proven by counting-fake and
privatized-second-window regressions (review fix).

**Rejected.** A general client-level RPC cache (wrapping the Telethon
client): touches the safety-critical session factory for no additional
measured repeat site — `attribution.py` and `quotes.py` already carry
their own per-run caches. Revisit only with `--profile`-style
whole-run RPC counting under the PROPOSALS performance-baseline work.

**Contract impact.** None: no flags, JSON, or exit codes change.
