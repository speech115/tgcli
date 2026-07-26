# 2026-07-26 — ADR-0059 verification infrastructure (Claude Fable 5)

**Did:** shipped the verification-infra slice: `tests/test_properties.py`
(seven derandomized hypothesis properties pinning the bf-01/bf-02/af-08
defect classes plus chatref totality), a PR-gated `test-macos` CI job
(red-first via `test_repository_config.py`), and `pytest -q -n auto` in
gate + CI via pytest-xdist (measured: 1431 tests in 9.2 s vs ~22–30 s
serial, no order-dependent failures). New dev deps `hypothesis`,
`pytest-xdist` recorded in ADR-0059.

**Decided:** properties run with `derandomize=True` — the gate must stay
deterministic; exploration is a local, flag-off activity. macOS leg runs
the suite only (lint/pyright are platform-independent).

**Learned:** the very first hypothesis run reproduced the af-08 lesson in
one example: `CloneState.from_dict({})` raises `KeyError`, safe only
because `load()` — its sole caller — catches exactly
`(KeyError, TypeError, ValueError)`. The property now enforces that the
raise set stays inside what the boundary catches, which is a stronger and
more honest invariant than "always ValueError".

**Post-push fix (same session):** the first live `test-macos` run failed —
uv resolved `requires-python = ">=3.12"` to Python **3.14.6** on macOS
(ubuntu ran 3.12), and three tests that monkeypatch pathlib internals
(`Path.stat` vanish-mid-walk fakes) don't fire on 3.14's pathlib. Fix:
`.python-version` pins 3.12 so each CI leg varies the platform, never the
interpreter, matching ADR-0001's target; pinned red-first via
`test_ci_interpreter_is_pinned_to_the_project_target`.

**Next:** SQLite clone-state prototype benchmark (bf-19 quadratic I/O).
