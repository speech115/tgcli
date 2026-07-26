# 2026-07-26 — ADR-0062/0063 drafts: job session + tg changes (Claude Fable 5)

**Did:** drafted the two owner-decision ADRs that unblock the next
product step. ADR-0062 (proposed): named session roles —
`accounts login --role job` authorizes a second device whose own lock
frees the primary session during long jobs; no implicit fallback between
roles. ADR-0063 (proposed): `tg changes` foreground feed — opaque
updates-state cursor, explicit `message_delete` tombstones, a loud `gap`
object with an honest recover hint, `--wait` long-poll intended for the
job role, no state files. ISSUES.md FEED-001 blocker now points at both
drafts. Docs only; no code.

**Decided:** nothing — both ADRs are proposed, not accepted. The
second-session shape is the only FEED-001 lock candidate that changes no
process model, which is why it is the one drafted rather than lock
yielding or a runtime.

**Learned:** writing the gap contract forces the honest split the wacli
review predicted: creation history is recoverable after a gap,
edit/deletion history is not — so the gap object must carry a recover
hint for one class and a plain admission for the other.

**Next:** owner accepts/rejects ADR-0062 (and the 24/7-mirror question
stays open separately); if accepted, ADR-0062 ships first, ADR-0063
second, each as its own scoped plan.
