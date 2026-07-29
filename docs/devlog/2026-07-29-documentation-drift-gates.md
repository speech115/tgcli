## 2026-07-29 — Active documentation reconciliation and drift gates (Codex)

**Did:** audited current `main` at `v1.2.19`, reproduced the confirmed drift
classes as subprocess tests against `scripts/check-docs.py`, then extended the
gate and reconciled README, MAP, ISSUES, PROPOSALS, contributor rules,
and the PR checklist. README now discovers `tg changes`, lists
`--session-role`, scopes Telegram `random_id` confirmation to send/forward,
includes `clone refresh`, delegates timeout detail to CONTRACT, and describes
the 13-step benchmark as representative. The new gate derives root flags from
the parser, guide/ADR inventory from the tree, and benchmark coverage from its
AST; it also catches closed-DEVLOG routing, stale removed flags, equivalent
exhaustive benchmark claims, and unsafe retry guidance for consumed clone
previews. Final `./scripts/gate.sh`: Ruff check/format clean, architecture
passed, Pyright 0 errors, `1546 passed, 9 skipped`, coverage 23 namespaces,
documentation gate 24 guide pages / 25 releases / 0 problems.

**Decided:** ADR-0065. Active summaries are part of feature closure, while the
gate enforces only mechanically derivable invariants; CONTRACT and boundary
tests remain the authority for semantic JSON and safety behavior.

**Learned:** a green guide-only gate did not cover README, MAP, status
backlogs, or contributor workflow text. The same audit found the PR template
and CONTRIBUTING still directing sessions to ADR-0058's closed
`docs/DEVLOG.md`, so process documentation needs the same fail-closed
treatment as user documentation. Independent spec and standards review caught
unsafe clone retry wording and an attempted update to closed `docs/CLONE.md`;
both were corrected before publication.

**Next:** merge the tooling/docs slice without a release bump because the CLI
contract is unchanged.
