# ADR-0059: Verification infrastructure — macOS CI leg, property tests, parallel suite

Date: 2026-07-26
Status: accepted

## Context

The 1.2.16 campaign measured line coverage at 94% and still confirmed 64
defects, because the classes that survived — silent HTML truncation
(bf-01), astral UTF-16 offset desync (bf-02), crash-on-malformed-state
(af-08), platform-only behavior (bf-03, a macOS case-insensitivity
collision) — are invisible to example-based line coverage by construction.
The audit found them by spending millions of agent tokens; nothing in the
repo re-finds them automatically if they regress. Separately, CI runs only
on ubuntu while macOS is a first-class target, and the suite ran serially
(29.5 s of the gate's 52 s).

## Decision

1. **Property-based tests** (`tests/test_properties.py`) via `hypothesis`
   (new dev dependency) pin the audit's defect classes as invariants over
   generated input: plain-render identity, HTML escape round-trip (astral
   included), markup-free HTML identity, HTML render totality (PolicyError
   or a result, never another exception), markdown bold offsets counted in
   UTF-16 code units, `chatref.parse` totality, and
   `CloneState.from_dict` raising nothing outside the exception set its
   only caller wraps. All properties run `derandomize=True`: the gate must
   be deterministic, and a curious agent can drop the flag locally to
   explore.
2. **A PR-gated macOS CI leg** (`test-macos`, `macos-latest`) runs the
   test suite. Lint, pyright, and the doc gates are platform-independent
   and stay ubuntu-only; branch pushes stay ubuntu-only for cost.
3. **The suite runs parallel** with `pytest-xdist` (new dev dependency):
   `pytest -q -n auto` in both `scripts/gate.sh` and CI. Measured on the
   full suite: 1431 tests, 9.2 s parallel vs ~22–30 s serial, zero
   order-dependent failures — the suite's tmp_path/monkeypatch isolation
   held up.

## Rejected

- Coverage-threshold gating: 94% was already not the problem.
- Fuzzing the CLI binary end-to-end: the pure seams (formatting, chatref,
  state loaders) are where the audit's classes live; process-level fuzzing
  buys minutes of runtime for little added reach.
- A full macOS matrix (lint + pyright + gates): only runtime behavior is
  platform-dependent.

## Consequences

- Two dev-only dependencies (`hypothesis`, `pytest-xdist`); the runtime
  dependency set is unchanged.
- The first property run immediately reproduced the af-08 lesson:
  `CloneState.from_dict({})` raises `KeyError`, which is safe only because
  `load()` catches exactly that set — the property now documents and
  enforces that pairing.
- Gate wall-time drops from ~52 s to ~38 s despite 200 examples × 7
  properties joining the suite.
- macOS CI failures block PRs; a genuinely platform-broken test now
  surfaces before merge instead of on the owner's machine.
