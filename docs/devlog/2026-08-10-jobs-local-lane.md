## 2026-08-10 — jobs registry and local transcription lane

**Did:** implemented #187's first end-to-end ADR-0087 layer: account-scoped
SQLite/WAL generations and events, local lane flock/recovery, add/list/show/
cancel/run CLI, one-item archive transcription quanta, bounded-aging priority,
runtime retry state, and jobs inventory in `store stats`.

**Decided:** the local lane calls the archive command seam in-process and never
parses its own CLI output or opens Telegram. A cancel recorded during a quantum
wins atomically when the result checkpoints; a late cancel after that commit is
a terminal no-op.

**Learned:** alias validation belongs before composing the registry path, even
though account resolution already came from config. The regression test now
proves `../` cannot escape `TGCLI_STATE_DIR`; finite-number checks also reject
`NaN` before it can reach the process deadline or scheduler loop.

**Next:** independent review and squash #187 into the #146 integration branch;
then add the explicit-role Telegram lane in #188.
