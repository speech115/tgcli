## 2026-08-10 — a flood must not destroy finished work

**Did:** landed ADR-0083 (#169, #170). `transfer.download_resumable` is a
serial checkpointed `iter_download` loop; `clone/reupload.py` owns the
`<part>.offset` sidecar and resumes only when the recorded size still matches
the media. A short stream now raises instead of publishing the final name.
`clone init --commit` / `clone refresh --commit` moved from `consume_preview`
to `begin_commit` + `finish_commit`, so a mid-commit flood leaves the preview
retryable. `clone status` carries `discussion_linked` and prints the recovery
command for a half-initialized clone; `clone sync`'s refusal names it too.

**Decided:** the ADR-0072 posture is untouched — a flood still stops the run
and arms the cooldown; only the cost of the stop changed. The reupload
download drops to serial, superseding ADR-0047's download leg: a striped
transfer scatters its stripes so no byte count describes what it has, and
throughput arguments do not apply to a transfer that never completes. The
parallel part upload stays at 4.

**Learned:** the old fakes for the striped path yielded a single chunk and
relied on `download_striped`'s `truncate(size)` pre-allocation to make the
file look complete — the same sparse-file hazard ADR-0052's `.done` marker was
invented for, still living in the test doubles. Instance-assigning `__call__`
on a fake client does not intercept `tg(request)`; Python looks the special
method up on the type, so the flood double had to be a subclass.

**Next:** three stacked PRs (#176 → #177 → this one) awaiting independent
whole-diff review; nothing merged.
