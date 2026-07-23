# ADR-0037: Split clone quote fallback rendering from the async resolver

Date: 2026-07-23
Status: accepted

Builds on: ADR-0035 (ceilings, one-job modules), ADR-0036 (quote replies).

## Context

`clone/quotes.py` landed with ADR-0036 as the client-bound resolver: turn a
`replies` classification into a native `InputReplyToMessage` or a rendered
fallback. Three live-run fixes in the same session — forbidden-peer titles,
the `Переслано от:` source label, and `QUOTE_TEXT_INVALID` strip-and-retry —
pushed it 380 → 500 lines and three ceiling bumps. The file became the largest
module under `clone/` and mixed two jobs:

1. **Decide** whether a quote stays native (reachability probes, cross-leg
   anchor walks, thread placement, send-time degrade).
2. **Shape** a degraded quote (peer label, blockquote prefix, body composition,
   `quote_flattened` rows, stripping a stale fragment while keeping the reply).

ADR-0034 warned against shallow mechanical splits of clone hotspots. The
reason to split here is the opposite of that warning: the second job is already
a real seam. It is synchronous, free of a Telegram client, and unit-testable
against constructed plans — the same reason `replies.py` stayed out of
`quotes.py` in ADR-0036. Leaving both jobs in one file forces every fallback
tweak to reopen the async resolver and its reachability cache.

## Decision

1. **`clone/quote_fallback.py` owns rendered degradation.** Source label,
   blockquote prefix, peer display label, building a fallback
   `TransportPlan` (including `quote_flattened`), `apply_body`, and
   `drop_stale_quote`. No client, no reachability probe, no leg map walk.

2. **`clone/quotes.py` owns resolution and send-time retry.** `ResolveContext`,
   reachability cache, cross-leg / foreign native rebuild, thread placement,
   `resolve`, `degrade_to_fallback`, and `send_with_degrade`. It calls into
   `quote_fallback` when the outcome is a rendered loss.

3. **Public callers import the owner.** `commands/clone.py` takes `apply_body`
   from `quote_fallback` and resolution/send from `quotes`. Tests import
   constants and pure transforms from the module that owns them — no
   re-export layer (same rule as ADR-0035).

4. **Ceilings ratchet down with the split.** `quotes.py`'s ceiling drops to
   the post-split size; `quote_fallback.py` gets its own reviewed ceiling.
   Behaviour is unchanged: the existing `test_clone_quotes` suite is the
   proof, not a new contract surface.

## Consequences

- Fallback wording and UTF-16 prefix math can change without touching
  reachability or cross-leg walks.
- The resolver file is readable in one sitting again; growth in either job
  hits its own ceiling.
- One more module under `clone/` — accepted cost for a seam that already
  existed conceptually between `replies` (classify) and `quotes` (resolve).

## Alternatives rejected

**Leave the 500-line file and raise the ceiling again.** Cheap now, taxes
every later fallback tweak with the full resolver context, and repeats the
ceiling bump pattern that flagged the seam.

**Three-way split (peers / fallback / resolve).** Peer probing has no caller
outside `quotes.resolve`; extracting it would create a pass-through module
ADR-0034 already rejected as shallow.

**Re-export fallback symbols from `quotes` for stable imports.** Hides the
ownership the split is meant to make obvious, and contradicts ADR-0035's
"patch the owning module" rule.
