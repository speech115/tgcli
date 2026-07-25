# ADR-0049: Clone sync progress lines on stderr

Date: 2026-07-24
Status: accepted

## Context

`clone sync` is silent from launch until the final JSON document. On a
protected channel a single large video is minutes of network transfer
with zero output — observed live today as "hangs 10 minutes" (it was
quietly moving a 2500-chunk file) and as an empty background-task pane
for the owner. Agents have the same problem: no way to distinguish
"working" from "stuck" without `-v`, whose Telethon debug firehose is not
a progress channel.

CONTRACT §2 already reserves stderr for exactly this ("progress, hints,
warnings"); stdout purity is untouched. `media download` already has a
chunk-cadenced progress callback seam.

## Decision

1. **`clone sync` emits single-line progress events to stderr by
   default**, in human mode and `--json` alike:
   - per batch: `[sync <source_id>] <n>/~<total> · <transport>` where
     `<total>` is the best-effort approximate message count;
   - during a file transfer, every ~5 MB:
     `[sync <source_id>] <n>/~<total> · reupload · <filename> ·
     download|upload <done>/<size> MB (<pct>%)`;
   - phase lines for the comments leg and roster.
2. **Plain lines only.** No carriage returns, no cursor control, no
   colors — the same stream must read correctly in a terminal, a log
   file, `tail -f`, and an agent transcript. The last line always states
   the current activity.
3. **No flag.** Silencing is `2>/dev/null` (economy principle); `-v`
   coexists — progress lines and debug records interleave on stderr.
4. **One progress seam.** The chunk-cadence callback pattern from
   `media.py` (`PROGRESS_EVERY_CHUNKS`) is extracted/shared rather than
   re-implemented (ADR-0043 discipline), and ADR-0047's parallel
   transfer reports through the same callback.
5. **CONTRACT** documents the stderr line shape as informative (stderr
   remains non-contractual free text; the note prevents accidental
   stdout leakage, not line-format stability).

## Consequences

- The owner's background-task pane and any agent transcript show live
  state; "quiet minutes" become "download 62% of a named file".
- stderr is chattier by default; scripts that captured stderr expecting
  silence must filter or redirect (stderr was never contract data).
- Near-zero flood impact. Printing is local; the only network cost is one
  `messages.getHistory(limit=0)` for the `~total`, resolved lazily on the
  first batch that actually copies. A sync with nothing new — the agentic
  keep-up-to-date call — spends no extra RPC at all, and a FloodWait on that
  request arms the ADR-0045 cooldown and exits 5 like any other.
- Line format may evolve without a version bump (explicitly
  non-contractual); agents must not parse it as API.
