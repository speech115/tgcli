## 2026-07-27 — Allow ChatAdminRights / ChatBannedRights in `tg api` (Cursor Grok)

**Did:** cleaned the stale local `main` WIP and the abandoned
`codex/recover-claude-1.2.16` worktree. Kept the useful fragment: `tg api`
constructor conversion now accepts `ChatAdminRights` and `ChatBannedRights`
(needed by `channels.editAdmin` / `editBanned`; no `Input*` form exists).
Red tests first in `tests/test_api_conversion.py`; CONTRACT §6 and
`docs/guide/api.md` updated. Version bump left to the integrator (ADR-0058).

**Decided:** narrow allowlist addition beside the existing participants-filter
exception — not a general TL constructor opening.

**Learned:** yesterday's promote-on-clone session left this as an uncommitted
local patch; thumbs/DEVLOG noise around it was already upstream.

**Next:** merge as 1.2.17 (CONTRACT change) once CI is green.
