## 2026-08-10 — foreground persisted jobs decision

**Did:** converted the owner-approved #146 grilling into ADR-0087 and a
three-slice campaign: registry/local lane, Telegram workloads, then recurring
operations and refresh cutover.

**Decided:** keep the process model daemonless. Two foreground lane runners
share an account-scoped SQLite/WAL registry but hold independent process-life
flocks; only four typed checkpointed workloads are accepted. Session role is
explicit for Telegram, the account governor remains above every role, and
cancellation is cooperative.

**Learned:** the earlier proposal coupled a durable queue to a resident
runtime, but the live governor changed the problem: launchd already supplies
wakes, while the missing layer is durable ordering and state between those
wakes. A second process lifecycle is unnecessary.

**Next:** chart the three native sub-issues under #146, then start the local
lane slice with red tests.
