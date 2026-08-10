## 2026-08-10 — release 2.0.7 integration

**Did:** merged #186 (ADR-0086) into `main` as `80c1e6e` and prepared the
2.0.6 → 2.0.7 patch release with its version files, lockfile, CHANGELOG
section, compare link, and operator-facing rationale. The integration also
trued `clone.py`'s reviewed line ceiling from 1230 to 1257 after the strict
architecture check identified the accumulated delta.

**Decided:** shipped the reporting fix independently. The clone command keeps
the same JSON, plain output, and exit semantics; the release boundary names
the version where durable progress can no longer outlive its only degradation
report.

**Learned:** issue #184 closed automatically when the squash merge reached the
default branch. Reporting after the checkpoint, rather than only at run end,
preserves both the safety invariant and honest diagnostics when a later flood
returns exit 5.

**Next:** verify the Release tag workflow publishes `v2.0.7` from the release
commit, then begin the owner-approved #146 scheduler campaign.
