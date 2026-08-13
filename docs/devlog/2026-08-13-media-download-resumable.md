## 2026-08-13 — Media serial download uses the shared resumable transfer

**Did:** completed thermos T31. `commands/media.py` now delegates serial bytes,
fsync-before-checkpoint ordering, progress cadence, and failure unwind to
`transfer.download_resumable`. The media command still owns source/media
identity, destination state, short-stream refusal, and atomic publication.
The duplicate serial loop and checkpoint helper were deleted. A public-seam
test pins the delegation; ADR-0091 regressions remain on the shared path.

**Decided:** T31 still applied after ADR-0091: that P0 fixed completeness and
durability, but explicitly deferred this ownership cleanup. No ADR or contract
edit: the ticket classifies unchanged CLI/state semantics as a small fix, and
the existing state JSON shape is preserved. The shared helper's 8-chunk
checkpoint cadence replaces media's private 16-chunk cadence.

**Learned:** T03 absorbed the corruption risk, not the parallel implementation.

**Next:** independent whole-diff Spec + Standards review before merge.
