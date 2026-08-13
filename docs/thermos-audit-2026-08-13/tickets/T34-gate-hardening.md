---
id: T34
priority: Debt
labels: [enhancement, needs-triage]
slice: Gates & contract surface
title: "Gate hardening: CI --strict, write-policy coverage, store scan registry"
---

## Problem

CI/gate.sh omit `--strict` (50-line grace). Coverage matrix does not
enforce write denylist. `check-docs` is incident-regex grab-bag.
`commands/store.py` inventory is copy-paste without ceiling.
`STATE_WRITER_MODULES` is fail-open allowlist.

## Expected

Owner picks which enforcements to turn on. Minimum useful pack: CI
`--strict` or documented grace; write-policy cross-check for api; table-
driven `store.scan`; consider deny-by-default for `write_text` under
`src/tgcli/`.

## Evidence

- Thermos Wave 9 security Medium#4–6 + quality
- Full-lane (enforcement mechanisms)

## Acceptance

ADR if changing gate semantics. False-positive rate acceptable; tests
updated.
