## 2026-08-13 — Account-bound `tg changes` cursors (ADR-0103)

**Did:** Thermos T14. Added four CLI-seam regressions; all failed on the old
codec (public init emitted `v1:`, and forged, cross-account, and legacy cursors
all polled successfully). Public init/results now emit HMAC-SHA256 `v2:`
cursors, and public polling validates the selected account binding before any
Telegram request. The local archive keeps its trusted `v1:` cursor path.
Updated CONTRACT §12, the change-feed guide, README, SKILL, MAP, and ADR index.

**Decided:** Fail closed exactly at the public agent-fed seam. The binding key
is derived from account alias/API id/API hash, excludes session role, and is
never persisted or emitted. Legacy public `v1:` cursors must re-init; silently
upgrading caller state would bless the forgery this ticket closes.

**Learned:** ADR-0093, 0096, 0097, 0099, and 0100–0102 were already present on
parallel remote branches, so this branch used ADR-0103 rather than colliding in
the wave's reserved range.

**Next:** Full gate is green (`1911 passed, 9 skipped`; 23 namespaces; 27 guide
pages; 44 releases; zero docs problems). Hand off the whole diff for
independent Spec + Standards review; no live smoke is needed for a local
pre-request refusal with exact RPC-absence assertions.
