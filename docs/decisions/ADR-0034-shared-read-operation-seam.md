# ADR-0034: Shared typed read-operation seam

Date: 2026-07-23
Status: accepted

Builds on: ADR-0026 (maintenance mode), ADR-0032 (read-only batch).
Spec: GitHub issue #23. Implementation tickets: #24, #25.

## Context

The interactive CLI and `tg batch` are two adapters for the same thirteen
read operations. Before this decision, each adapter independently knew the
operation names, input coercion, defaults, command-module selection, and error
details. The duplication was not theoretical: batch ISO date filters reached
read modules as strings until `0570cbd`, while the interactive adapter already
coerced those values to timezone-aware datetimes.

The scoped architecture survey also found larger physical hotspots:

- `src/tgcli/cli.py`: 1,002 lines, 29 touches and 817 changed lines since
  2026-07-17;
- `src/tgcli/commands/clone.py`: 874 lines;
- `src/tgcli/clone/state.py`: 287 lines;
- clone-related tests: 5,478 lines.

Clone has had no functional change since its 2026-07-17 acceptance. A
mechanical split there would create shallow modules and risk the live-proven
workflow. The two active read adapters instead form a real seam with current
change frequency and a reproduced locality failure.

## Decision

1. `tgcli.read_ops` is the deep module for the thirteen read operations shared
   by interactive CLI and JSONL batch. Its external interface prepares an
   operation from either adapter and executes it. Its closed typed operation
   sum is internal implementation, not a new CLI contract.
2. The interactive adapter retains argparse grammar, search-shape validation,
   process lifecycle, config/session selection, output, timeouts, and exit
   mapping. The batch adapter retains JSONL parsing, allowlist/cap enforcement,
   sequential execution, fail-fast, per-operation error envelopes, and
   FloodWait mapping.
3. Date parsing has one implementation. Adapter-specific invalid-input
   callbacks preserve interactive exit 1 and batch policy exit 2.
4. Existing command modules continue to own Telegram calls and data
   projections. The refactor adds no RPC, flag, JSON field, TSV column, exit
   code, safety change, or Telegram mutation.
5. CI runs `scripts/check-architecture.py`. It enforces:
   - zero direct imports of exclusive shared-read command modules from
     `cli.py` and `commands/batch.py`; batch also may not import the mixed
     `media` command module, and neither adapter may dispatch
     `media.manifest` directly;
   - reviewed exact baselines of `cli.py` 909 lines, `commands/batch.py` 96,
     `read_ops.py` 413, `commands/clone.py` 874, and `clone/state.py` 287.
6. The ownership target is immediate: all shared read dispatch lives behind
   the seam. The size target is monotonic: future substantive deepening lowers
   a baseline in the same change; both growth and shrinkage without updating
   the reviewed baseline fail CI. Ordinary work may not raise one without a
   new reviewed architecture decision. Clone test lines are measured debt, not
   a CI budget, because maintenance fixes must add reproducing tests.

## Consequences

- Read coercion and command selection have locality: a shared correction lands
  once and both adapters receive it.
- The module has leverage across thirteen operations and two adapters while
  both public CLI seams remain the test surface.
- Removing the module would force the dispatch and coercion ladders back into
  both adapters, so it passes the deletion test.
- The ratchet prevents the proven duplication from returning and records
  current clone debt without incentivizing shallow pass-through splits.
- `docs/CONTRACT.md` is unchanged because observable behavior is unchanged.
