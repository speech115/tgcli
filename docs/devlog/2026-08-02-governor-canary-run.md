## 2026-08-02 — Live canary for the request governor's pacing (Claude)

**Did:** ran the owner-approved canary from issue #140 against the live `main`
account and posted the evidence comment; closed #140 and updated #141 with what
the evidence does and does not unblock. 27 requests (24 `read`, 2 `dialogs`, 1
`message`), 15 distinct peers, 2130 messages, 0 mutations, 0 media transfers,
138 s wall clock. **All 27 exited `0`; no `FLOOD_WAIT` or any flood-family
error, no `retry_after`, no abort.** No production code changed.

**Decided:** two owner calls before the run. The `D1`…`D15` selection
(checklist item 7, the ticket's standing blocker) was assembled offline from
local state only — `archive.db` scope rows and clone metadata — so no network
request was spent on selection and the 27-request budget stayed exactly the
protocol's. Second, §7's stop rule got one narrow amendment: `NOT_FOUND` (exit
4) in Phase B skips that dialog and records it rather than aborting the run,
because a deleted lab mirror carries no rate-limit information. Every other
non-zero exit and every flood-family error kept full abort force. The carve-out
was never exercised — all 15 dialogs resolved.

**Learned — the run was more conservative than the interval it was meant to
test.** The protocol paced with `sleep 3` *after each invocation returned*, so
the realized interval was `intended + latency`: median 4.84 s, not 3 s (median
request latency 1.82 s). The canary therefore demonstrated that ~4.8 s
start-to-start is safe; it did not test 3 s start-to-start. ADR-0072's
decision 3 never says which of the two its intervals mean, and a governor
sleeping 3 s *from request start* would issue requests ~60% faster than
anything observed here. Unlike the ADR's two stated assumptions, this is not a
known-unknown that acceptance can carry — it is an underspecification the
implementation would otherwise settle by accident. Flagged on #141 as an
ADR-text fix required before `accepted`.

**Learned — the breadth signal came back null.** Rate fixed, breadth varied
3 → 12 distinct peers. The phases are indistinguishable: median gap 4.84 s in
both, median duration 1822 vs 1824 ms, zero errors in both. Per the protocol's
own pre-registration this fails to falsify the rate-vs-breadth hypothesis
rather than establishing anything; 15 peers is 15% of the 100/24 h budget and
the incident spanned 791, so the budget is untouched, not validated. Assumption
1 (does a request during a penalty extend it) stayed unanswerable by design —
no penalty occurred, and the stop rule forbade the follow-up request that would
have observed one.

**Learned — the journal cross-check confirms the CONTRACT §9 gap is total.**
The 27 rows in `invocations.jsonl` for the run window carry only `timestamp`,
`command`, `account`, `exit_code`, `duration_ms`. No `retry_after`, no governed
request type, no refusal provenance, no stop reason. The §9 field additions
ADR-0072 describes start from zero, not from a partial implementation.

**Also learned — the timing gate closed itself.** The owner's amendment asked
for a settling interval past the `2026-08-01T12:27:47Z` deadline rather than a
start at the boundary. That was satisfied without an artificial wait: the
account had already been used normally after expiry (last prior invocation
2026-08-01T19:42Z, exit 0), so this was not a cold-start-into-a-just-lapsed-
penalty measurement — which would have been a different experiment.

**Next:** #141 flips ADR-0072 to `accepted` once the start-to-start ambiguity
is pinned down in the ADR text, then lands the `docs/CONTRACT.md` changes with
the integrator's version call (the exit-5 → exit-0 move for a scheduled pass
under cooldown is a contract break), CHANGELOG and version bump, closes #120
with a resolution pointer, and notes the follow-up map for the job model that
#131 ruled out of scope.
