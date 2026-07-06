# DEVLOG

Append-only session log. Newest entry on top. Every agent session that
touches this repo adds one entry (AGENTS.md rule).

Template:

```markdown
## YYYY-MM-DD — <short title> (<agent/model>)
**Did:** what actually changed (files, commands, results)
**Decided:** decisions made + link to ADR if architectural
**Learned:** surprises, gotchas, dead ends worth remembering
**Next:** the single most useful next step
```

---

## 2026-07-06 — Scope grill: "all functions" resolved via raw passthrough (Claude Fable 5)
**Did:** grilled the "new version with ALL Telegram functions" request;
competitor survey (iyear/tdl 7.7k★ media-only; b1rd33/tg-cli — closest analog,
62 commands, MIT, bus-factor 1; ~10 telegram-mcp servers). Ran a loophole
cycle on the strategy until it converged (3 iterations, 6 major holes fixed).
Added ADR-0008, docs/FEATURES.md, CONTRACT §6 (tg api), PLAN updates
(non-goals, phases 2/4, new phase 7, risks, research addendum), MAP rows.
**Decided:** "all functions" = wrapped commands for daily use + `tg api`
raw TL passthrough for the long tail + FEATURES.md coverage matrix with
explicit exclusions (ADR-0008). Source of truth = pinned Telethon TL schema.
Do not fork b1rd33/tg-cli; borrow typed `--confirm` + single-use previews.
**Learned:** loopholes found by the cycle: raw passthrough would have
bypassed preview→commit (fixed: read-only until phase 4, `--write` gate);
`export*` methods look like reads but mutate (fixed: strict verb allowlist);
`auth.logOut` via passthrough would kill the managed session (fixed: hard
denylist); TL output can't obey our JSON stability rules (fixed: CONTRACT §6
exemption); secret chats/calls are impossible in Telethon (fixed: explicit
exclusions, otherwise "all functions" acceptance is unfalsifiable).
**Next:** execute phase-1 plan (unchanged by this session).

## 2026-07-06 — Phase 0: project born (Claude Fable 5)
**Did:** researched gogcli internals (deepwiki) and Telethon session/flood
semantics (context7); created docs-first scaffold: README, AGENTS, CLAUDE,
MAP, CONTRACT, PLAN, ADR-0001…0007, this DEVLOG, phase-1 TDD plan.
**Decided:** Python+Telethon over Go rewrite (ADR-0001); stateless CLI-first,
no daemons, MCP non-goal (ADR-0002); output contract with fixed exit codes
(ADR-0003); per-account SQLiteSession + file lock, import sessions from old
stack (ADR-0004); preview→commit write safety with audit log (ADR-0005);
TDLib as media fallback only (ADR-0006); MAP+ADR+DEVLOG discipline (ADR-0007).
**Learned:** gogcli has NO MCP server — it's an explicit non-goal in their
spec; agents drive it purely via CLI + SKILL.md. That validates dropping the
daemon layer entirely. Telethon's entity cache in the session file is the
key enabler for cheap short-lived processes.
**Next:** execute phase-1 plan (docs/superpowers/plans/2026-07-06-phase-1-core-and-read.md).
