# Phase 7 Coverage Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pinned Telethon TL namespace coverage matrix machine-checkable and fail closed when the installed layer or its classifications drift.

**Architecture:** `docs/FEATURES.md` will contain exactly one normalized row for every module in `telethon.tl.functions`, with non-TL exclusions moved to a separate prose section. `scripts/check-coverage.py` will discover namespaces through `pkgutil`, parse the Markdown table without external dependencies, and compare the two sets exactly. It exits non-zero for missing, unknown, duplicate, malformed, or unjustified excluded rows.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `pathlib`, `pkgutil`, `re`, `sys`), installed Telethon 1.44, pytest.

## Global Constraints

- The pinned Telethon layer is the source of truth; never fetch a network schema.
- The checker is read-only and writes no state outside the repository.
- stdout is one human-readable validation result; failures name exact matrix defects.
- Every installed namespace must use exactly one of `wrapped`, `api`, `planned:<phase>`, or `excluded`.
- Every `excluded` namespace must have a non-empty reason.
- The matrix must not contain pseudo-namespaces; secret chats, Bot API, and signup remain prose exclusions.
- Write a failing test before checker implementation; run `pytest -q` before every commit.

---

### Task 1: Normalize the matrix to the pinned Telethon namespace set

**Files:**
- Modify: `docs/FEATURES.md`
- Test: `tests/test_check_coverage.py`

**Interfaces:**
- Consumes: `telethon.tl.functions` namespace list from the pinned environment.
- Produces: one Markdown table with columns `TL namespace`, `Status`, `Notes` and exactly these namespace keys: `account`, `aicompose`, `auth`, `bots`, `channels`, `chatlists`, `contacts`, `folders`, `fragment`, `help`, `langpack`, `messages`, `payments`, `phone`, `photos`, `premium`, `smsjobs`, `stats`, `stickers`, `stories`, `updates`, `upload`, `users`.

- [x] **Step 1: Write a failing matrix test**

```python
def test_current_features_matrix_covers_the_installed_layer():
    matrix = module.parse_matrix(FEATURES)
    assert set(matrix) == module.discover_namespaces()
```

- [x] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_check_coverage.py::test_current_features_matrix_covers_the_installed_layer -q`
Expected: FAIL because the checker module does not exist.

- [x] **Step 3: Replace the provisional table**

Use one row per namespace. Classify daily command namespaces as `wrapped`, general raw-TL namespaces as `api`, and `auth`, `phone`, `updates`, and `upload` as `excluded` with their policy reasons. Move secret chats, Bot API, and signup beneath `## Non-TL exclusions`; do not put them in the namespace table.

- [x] **Step 4: Keep the matrix test red until Task 2 supplies the parser**

The documentation edit intentionally does not make a missing checker test green.

### Task 2: Implement the fail-closed checker

**Files:**
- Create: `scripts/check-coverage.py`
- Modify: `tests/test_check_coverage.py`

**Interfaces:**
- Consumes: `discover_namespaces() -> set[str]`, `parse_matrix(path: Path) -> dict[str, tuple[str, str]]`.
- Produces: `validate(features_path: Path, namespaces: set[str] | None = None) -> list[str]`, where an empty list means valid; `main(argv: list[str] | None = None) -> int` with optional `--features PATH`.

- [x] **Step 1: Extend failing tests**

```python
def test_validate_reports_missing_unknown_duplicate_and_bad_status(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text(
        "| TL namespace | Status | Notes |\n"
        "|---|---|---|\n"
        "| account | api | raw TL |\n"
        "| account | wrapped | duplicate |\n"
        "| unknown | future | bad |\n"
    )
    errors = module.validate(features, {"account", "auth"})
    assert any("duplicate namespace: account" in error for error in errors)
    assert any("unknown namespace: unknown" in error for error in errors)
    assert any("invalid status for unknown: future" in error for error in errors)
    assert any("missing namespace: auth" in error for error in errors)
```

Also add one fixture with `| auth | excluded | |` and assert the validation
contains `excluded namespace auth needs a reason`.

- [x] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_check_coverage.py -q`
Expected: FAIL because `scripts/check-coverage.py` is absent.

- [x] **Step 3: Implement minimal checker**

```python
VALID_STATUSES = {"wrapped", "api", "excluded"}

def discover_namespaces() -> set[str]:
    from telethon.tl import functions
    return {module.name for module in pkgutil.iter_modules(functions.__path__)}

def valid_status(status: str) -> bool:
    return status in VALID_STATUSES or bool(re.fullmatch(r"planned:[1-9][0-9]*", status))
```

Parse only three-cell Markdown rows whose first cell matches
`[a-z][a-z0-9]*`; ignore the header and separators. Preserve duplicates long
enough for `validate` to report them. Print `coverage OK: <count> namespaces`
and return 0 when validation returns no errors; otherwise print each error to
stderr and return 1.

- [x] **Step 4: Run GREEN and full suite**

Run: `.venv/bin/pytest tests/test_check_coverage.py -q && .venv/bin/python scripts/check-coverage.py && .venv/bin/pytest -q`
Expected: all checker tests pass, the script reports `coverage OK: 23 namespaces`, and the full suite passes.

- [x] **Step 5: Commit checker and matrix**

```bash
safe-commit "Add Telethon coverage closure gate" docs/FEATURES.md scripts/check-coverage.py tests/test_check_coverage.py
```

### Task 3: Close Phase 7 documentation

**Files:**
- Modify: `docs/PLAN.md`
- Modify: `docs/MAP.md`
- Modify: `docs/DEVLOG.md`

**Interfaces:**
- Consumes: a successful `scripts/check-coverage.py` run against the pinned layer.
- Produces: Phase 7 marked accepted in PLAN, script marked `[done]` in MAP, and a DEVLOG entry that records the exact checked namespace count and test output.

- [x] **Step 1: Update phase status and map**

Mark Phase 7 complete only after the checker exits 0. Replace the MAP script row with `[done]` and describe the exact namespace-matrix gate.

- [x] **Step 2: Record the verification**

Add a newest-first DEVLOG entry with `coverage OK: 23 namespaces` and the full test command result.

- [x] **Step 3: Verify and commit**

Run: `.venv/bin/python scripts/check-coverage.py && .venv/bin/pytest -q && git diff --check`
Expected: checker exit 0, all tests pass, no whitespace errors.

```bash
safe-commit "Close Phase 7 Telethon coverage gate" docs/PLAN.md docs/MAP.md docs/DEVLOG.md
```

## Self-Review

- Spec coverage: Task 1 makes each installed namespace explicit; Task 2 fails on all set and classification drift; Task 3 closes the phase only after the checker passes.
- Placeholder scan: no implementation placeholders; every code task has concrete behavior, tests, and commands.
- Type consistency: `parse_matrix` feeds `validate`; `main` is the only command-line surface and uses `validate` directly.
