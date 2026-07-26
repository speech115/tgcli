# 2026-07-26 — PR #88 review fixes: degrade guard + devlog placement (Claude Fable 5)

**Did:** closed both findings from the independent whole-diff review of
PR #88. MAJOR: ADR-0061's entity reuse had silently disabled the
comments-leg graceful degrade for windows 2+ — a source group privatized
mid-run escaped as a raw exit 1 instead of `comments: "unavailable"`.
Red-first regressions (`test_privatized_source_degrades_on_a_cached_window`
failing with ChannelPrivateError before the fix, plus a FloodWait-still-
escapes pin), then the minimal fix: the window's source iterator carries
the same degrade guard as the first-window resolve; `copy_batch` failures
keep escaping unchanged. ADR-0061's incorrect justification rewritten to
record what review disproved. MINOR: the process-speed session entry
moved out of the closed `docs/DEVLOG.md` into
`docs/devlog/2026-07-26-process-speed-data.md`.

**Decided:** degrade detection now lives at the read seam, which is
strictly broader than the old per-window re-resolve (a mid-window flip
degrades too, where pre-ADR-0061 code crashed) — reviewed behavior, not
an accident.

**Learned:** "the existing error paths will catch it" is exactly the kind
of claim an independent reviewer must re-derive: the error *classes* were
the same, the *handling* was not. The review reproduced it empirically
before believing it — the standard the campaign set.

**Next:** green CI on PR #88 (test + test-macos), then merge.
