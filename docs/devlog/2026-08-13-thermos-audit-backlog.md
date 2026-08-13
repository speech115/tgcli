## 2026-08-13 — Whole-repo thermos audit backlog (Composer)

**Did:** Ran dual-pass thermos audits across nine slices (Safety, Session,
Governor/jobs, Clone, Archive, Media, Read, CLI shell, Gates). Captured
37 ticket-ready items under `docs/thermos-audit-2026-08-13/` plus index
`docs/thermos-audit-2026-08-13-backlog.md` and publisher
`scripts/publish-thermos-backlog.py`. Updated `docs/MAP.md`. GitHub
issue create is blocked for this cloud token (`Resource not accessible`);
owner publishes with `--apply` when issues:write is available.

**Decided:** Document-only backlog PR (no code fixes this session). P0
safety net first (governor degraded/arm, media completeness, api write
denylist), then P1 integrity, then debt under owner gate.

**Learned:** Async thermos subagent limit is 10; queue remaining passes.
Quality and security often overlapped on ceiling-hot files
(`commands/clone.py`, `jobs/store.py`, `archive/store.py`, `cli.py`/`parser.py`).

**Next:** Owner publishes issues (`python3 scripts/publish-thermos-backlog.py --apply`)
or starts fix-slice T01–T04.
