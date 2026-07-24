# ADR-0043: Process hardening — shared atomic/lock/TTL primitives, enforced

Date: 2026-07-24
Status: accepted

## Context

The independent review of the 1.2.0 stack (DEVLOG 2026-07-24) found that
every code defect was the same failure shape: a safety pattern existed in one
subsystem and was re-implemented — without the guard — in a parallel one.
Previews were classified with an mtime fallback, logins were not (the
critical cleanup-vs-live-login race); config rewrites were atomic, attempt
writes were not; `doctor` probed locks without side effects, `accounts show`
re-implemented the probe and created stray `.lock` files; `--account`
resolution exits 3 while a near-identical lookup drifted toward 4
undocumented. Convention alone did not transfer the patterns; only shared
code and fail-closed gates do.

## Decision

1. **One writer for state files.** `tgcli/atomic.py` owns atomic replacement
   (same-dir temp file, fsync, chmod 0600, `os.replace`). Every module that
   writes files read back later under the config/state roots uses it;
   `scripts/check-architecture.py` fails on any `write_text` call in the
   listed state-writing modules. Exports and self-deleting probe files are
   exempt — nothing re-reads them as state.
2. **One lock probe.** `session.lock_held()` is the only LOCK_NB probe; it
   never creates a lock file for a missing session (CONTRACT §5.1) and never
   holds the lock. `doctor` and `accounts show` both call it. Holding a lock
   during a mutation (`accounts remove`) remains a separate, deliberate flow.
3. **One TTL classifier.** `store.py` classifies every `expires_at`-carrying
   record through a single helper with the mtime fallback, parameterized by
   TTL. A new bucket cannot ship without the torn-write guard.
4. **The exit-code table is executable.** `tests/test_contract_exit_codes.py`
   pins CONTRACT §4 (class → exit → code) plus the documented lookup-vs-
   resolution fork, so a new command cannot silently redefine a code.
5. **The gate is one command.** `scripts/gate.sh` runs the exact CI steps;
   AGENTS.md now points at it instead of an inline command list. CI runs on
   every branch push (not only PRs and main), with per-ref concurrency
   cancellation.
6. **Process rules move from DEVLOG into law.** AGENTS.md gains: the
   mirror-fix rule, wait-for-green-CI before merge, review fixes ride the PR
   head, DEVLOG live-detail redaction. Stacked-release mechanics live in
   `docs/agents/release.md`. A repo-local `reviewer` subagent definition
   (`.claude/agents/reviewer.md`) carries the review briefing and has shell
   access, so reviews need no per-session re-briefing.

## Consequences

- Three helper seams (`atomic`, `lock_held`, `_classify_ttl_record`) replace
  five divergent copies; the bug class that produced the 1.2.0 critical is
  now structurally unrepresentable in checked modules.
- The architecture gate grows its first content rule (an AST call ban) beyond
  ownership and ceilings; future write-path modules must be added to
  `STATE_WRITER_MODULES` to stay honest.
- No CLI surface, JSON shape, or exit code changes — CONTRACT.md is
  untouched and no release is required (ADR-0038 does not trigger).
