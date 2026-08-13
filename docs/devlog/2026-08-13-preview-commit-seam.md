## 2026-08-13 — Table-driven preview-to-commit seam (GPT-5.6 Sol)

**Did:** Implemented thermos T27 under ADR-0108. Added one registry covering
send, edit, delete, forward, draft set/clear, and clone init/refresh. Preflight,
network dispatch, CLI audit details, and successful finish timing now select
the same descriptor instead of maintaining parallel conditionals.

Deleted the dead burn-before-work `consume_preview` function and moved its
remaining tests to the retryable `begin_commit`/`finish_commit` state machine.
The focused safety and mutation suites pass 212 tests.

**Decided:** Command modules still own payloads and exact Telegram requests.
The registry owns orchestration only. Result audits remain before
`finish_commit`, so audit failure leaves the preview pending and retryable.

**Learned:** ADR-0083 had removed every production caller of the legacy seam,
but test helpers kept it looking supported after its safety model was retired.

**Next:** Integrator needs no release bookkeeping because the CLI contract is
unchanged. Independent whole-diff Spec + Standards review remains required.
