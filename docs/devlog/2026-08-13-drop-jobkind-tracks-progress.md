## 2026-08-13 — Drop unused JobKind.tracks_progress (Composer)

**Did:** Removed `tracks_progress` from `JobKind` and all registry
constructors. Updated ADR-0114 to describe kind→lane only; deduped
`jobs/model.py` / `jobs/runner.py` blurbs in MAP.

**Decided:** Progress metadata stays out of the registry until something
reads it (Wave E quality finding on PR #276).

**Learned:** Registry fields without readers are YAGNI debt, not
forward-looking API.

**Next:** Gate + push `cursor/jobkind-registry-a379`.
