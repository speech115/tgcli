## 2026-08-13 — Batch JSONL strict bool/int coercion (Composer)

**Did:** `_batch_bool` / strict typed batch fields in `read_ops.from_batch`
so `"false"` cannot become True. CLI batch tests. Thermos T15.

**Decided:** Small-fix aligning batch with CLI argparse semantics; no ADR
unless CONTRACT batch section needs a line (types already implied).

**Learned:** none.

**Next:** Continue thermos backlog.
