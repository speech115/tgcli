# ADR-0107: Strict and deny-by-default repository gates

Date: 2026-08-13
Status: accepted
Amends: [ADR-0043](ADR-0043-process-hardening.md) decision 1 and
[ADR-0058](ADR-0058-process-speed-revisions.md) decision 3.

## Context

Thermos debt T34 found four fail-open seams in the repository checks. The
mandatory local and CI gates invoked the architecture checker without
`--strict`, so a reviewed ceiling could grow by 50 lines on every feature
branch. The coverage matrix described raw API exclusions without mechanically
binding them to `WRITE_NAMESPACE_DENYLIST`. The atomic-write check scanned only
the manually maintained `STATE_WRITER_MODULES`, so a new module was unchecked
until someone remembered to register it. Finally, `commands/store.py` repeated
file-bucket glob logic and had no architecture ceiling.

## Decision

1. `scripts/gate.sh` and the Ubuntu CI leg invoke
   `scripts/check-architecture.py --strict`. Direct diagnostic invocation may
   still omit `--strict` and show ADR-0058's grace warning, but every mandatory
   gate uses zero grace. Ceiling-number reconciliation remains integrator-owned.
2. The architecture checker scans every Python module under `src/tgcli/` for
   bare `write_text` calls. There is no writer-module allowlist to forget.
   Doctor's self-deleting writability probe uses an explicit ephemeral file
   open, so the production tree needs no exemption.
3. `docs/FEATURES.md` gains the machine-readable `raw-denied` status.
   `check-coverage.py` requires the namespaces with that status to equal
   `commands.api.WRITE_NAMESPACE_DENYLIST` in both directions. `account` and
   `auth` carry the status; `phone` remains an unsupported product namespace,
   not a wholesale raw-API write denial.
4. `commands/store.py` declares simple file inventory buckets in one registry
   consumed by `scan()`. Custom TTL, lock, directory-tree, and jobs inventory
   logic stays specialized. The module enters the strict architecture ratchet
   at its post-refactor size.

## Rejected alternatives

- Keep the 50-line grace in mandatory gates and document it again: rejected
  because T34 explicitly selects strict CI/local enforcement, and the existing
  documentation did not make the mandatory checks a zero-grace ratchet.
- Keep `STATE_WRITER_MODULES` and strengthen review reminders: rejected because
  a renamed or newly added module would still be unchecked by construction.
- Infer raw API denial from prose in the FEATURES notes: rejected as another
  incident-specific regex. A status value is explicit and parseable.
- Restructure `check-docs.py` in this slice: rejected as unrelated to the three
  named T34 deliverables. Its semantic README checks remain enforced; replacing
  them needs a separate decision with equally strong coverage.

## Contract impact

None. CLI flags, JSON/plain output, exit codes, Telegram safety behavior, and
dependencies are unchanged. This is enforcement and internal inventory work;
`docs/CONTRACT.md`, versions, and `CHANGELOG.md` are untouched.
