## 2026-07-31 — Archive Phase 5 review fixes (Codex)

**Did:** addressed the pre-merge Phase 5 review on the existing feature
branch. Registered a Unicode-aware SQLite casefold function for `--from`,
translated malformed FTS5 MATCH expressions into the documented usage error,
removed the superseded duplicate search implementation, and corrected
`tg_link` generation for users, public groups/channels, private
groups/channels, and basic-group fallback. Added permanent regressions for
Russian sender names, malformed MATCH, and each link form. Focused archive
coverage is 62 passed; ruff and focused pyright are clean.

**Decided:** review fixes stay on the Phase 5 branch and are not deferred to
Phase 6. The Phase 5 query surface remains the sole search implementation;
`archive/search.py` owns only MATCH normalization and peer resolution.

**Learned:** SQLite built-in `lower()` is ASCII-only, so Python `casefold()`
must be registered at the connection seam for Russian names. Telegram's
message deep links distinguish user, public group/channel, and private
group/channel forms; a generic `chat_id` URI is not a universal contract.

**Next:** run the full repository gate, commit and push this review-fix
commit, then hand the branch to the integrator for ceiling ratchet and the
1.2.24 release. Phase 6 stays separate.
