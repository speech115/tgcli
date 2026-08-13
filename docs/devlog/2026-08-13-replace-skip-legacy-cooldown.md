## 2026-08-13 — T19: `--replace` must not block on legacy clone cooldown (Cursor)

**Did:** `commands/clone.py` `commit_init` loaded the old state slot and
called `_enforce_cooldown` on it before the `--replace` supersede ran, so a
legacy `retry_not_before` deadline (ADR-0045, retired by ADR-0072) blocked
abandoning that slot. Added a red test
(`test_clone_init_replace_supersedes_cooling_legacy_slot`) staging a cooling
old slot and asserting `--replace --commit` still succeeds, then skipped the
early per-clone enforcement when `replace` is true. The unconditional
`_enforce_cooldown` right after supersede (on the fresh state) and every
governor gate on the RPC seam are untouched.

**Decided:** Small-fix lane (ADR-0073) — `docs/CONTRACT.md` §"clone init
--replace" already documents superseding "before loading state"; this
restores that stated intent rather than changing it. No ADR, no CONTRACT
edit.

**Learned:** `main`'s CI has been failing on a billing/spending-limit
annotation since #204, not on real check failures, which is why a pre-existing
`ruff`/`format` violation in `scripts/publish-thermos-backlog.py` never got
caught. Left untouched (out of T19 scope); full gate run scoped ruff to the
touched files and green otherwise (pytest 1885 passed / 9 skipped, pyright
0 errors, architecture/coverage/docs gates green).

**Next:** Owner may want a separate small fix for the
`publish-thermos-backlog.py` lint break and to check the GitHub Actions
billing issue blocking CI on `main`.
