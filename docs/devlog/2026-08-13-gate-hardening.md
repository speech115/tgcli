## 2026-08-13 — Strict and deny-by-default gates (ADR-0107)

**Did:** Implemented thermos T34's named gate pack.
The local gate and Ubuntu CI now run the architecture checker with `--strict`.
The checker scans every production Python module for bare `write_text`;
`STATE_WRITER_MODULES` and its rename/new-module blind spot are gone.
Doctor's disposable writability probe uses an explicit ephemeral file open.
FEATURES marks wholesale raw API exclusions as `raw-denied`, and the coverage
gate requires those rows to equal `WRITE_NAMESPACE_DENYLIST` both ways.
`store.scan` now gets simple file buckets from one registry and the module has
a reviewed strict ceiling; specialized TTL/lock/tree scans remain explicit.

**Evidence:** focused RED was 7 failed / 55 passed for the missing controls.
Focused GREEN is 62 passed across architecture, coverage, and store tests.
Full strict gate: 1914 passed / 9 skipped; ruff lint/format, architecture,
pyright (0 errors), 23-namespace coverage, and docs (0 problems) all passed.

**Decided:** mandatory checks use zero grace; direct checker diagnostics retain
ADR-0058's warning band. `check-docs` restructuring is outside this named pack.

**Learned:** the raw API status needed a distinct machine value because
`phone` is product-excluded but is not wholesale write-denied like auth/account.

**Next:** independent whole-diff Spec + Standards review before integration.
