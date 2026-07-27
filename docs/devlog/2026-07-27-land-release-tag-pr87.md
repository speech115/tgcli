## 2026-07-27 — Land PR #87 release-tag workflow (Cursor Grok)

**Did:** rebased `claude/fable5-hardening-5zqywp` onto `main` after #88;
resolved AGENTS.md against ADR-0058 (integrator release + compare-link +
Release-tag workflow); kept macOS CI tests alongside the new tag-workflow
guard; moved the closed-`DEVLOG.md` appends into
`docs/devlog/2026-07-26-release-tag-workflow.md`. Owner-local tag backfill
`v1.2.10`–`v1.2.16` already on origin; #72 closed. Full gate green
(1446 passed). Squash-merging #87.

**Decided:** ADR-0058 integrator mechanics stay; PR #87 only adds the
enforced compare-link gate and CI tag executor (no ADR supersede).

**Learned:** tagging commits that touch `.github/workflows/` needs the
`workflow` OAuth scope; without it only some tags push.

**Next:** confirm the Release-tag Actions run no-ops on this merge (version
unchanged) and fires on the next integrator version bump.
