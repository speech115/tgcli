## 2026-08-13 — Atomic incremental export batches (GPT-5.6 Sol)

**Did:** Implemented thermos T21 under ADR-0104. `export messages --append`
and `--resume` now copy the committed JSONL into a sibling temporary file,
stream the new batch there, fsync it, and atomically replace the destination.
Added a public CLI regression whose iterator stops after the first new row;
the old implementation exposed that row, while the fixed path leaves the
original file byte-for-byte unchanged. Updated CONTRACT §7, MAP, and the ADR
index. Focused export suite: 37 passed.

**Decided:** Use temp+rename rather than a checkpoint sidecar. Resume still
derives its cursor from the last row of the last fully published file; no new
persistent state or recovery schema is introduced.

**Learned:** Exception-time truncation cannot cover abrupt process death.
Atomic append costs a full sequential copy and temporary disk space, but keeps
the existing streaming memory bound and makes readers observe only old or new.

**Next:** Integrator assigns release bookkeeping; independent whole-diff review
must clear Spec and Standards before merge.
