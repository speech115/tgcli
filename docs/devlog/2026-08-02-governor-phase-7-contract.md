## 2026-08-02 — Governor phase 7: guides, and the contract texts for the integrator (Claude)

**Did:** completed the ADR-0072 implementation slice. The operator-facing
docs now describe a cooling account correctly, `docs/MAP.md` marks the
governor `[done]`, and the CONTRACT/CHANGELOG/version edits are written out
for the integrator to land at merge (ADR-0058: shared files belong to the
integrator; a feature branch never touches them).

**Guides.** `archive-refresh.md` explains the pacing, that governed sleep
does not count against `--timeout` (a hang detector), that `--max-runtime`
exhaustion is a *normal* stop with `stop_reason`, and the new scheduled-wake
contract: a partial cooldown defers, exits 0, alerts once at arming, and
later wakes are silent. `doctor.md` documents the `governor_cooldowns` and
`governor_degraded` checks and that doctor is the one command that works
while everything else refuses. `clone.md` drops the deleted `account_flood`
preview field and recasts exit 5 as a per-type cooldown refusal. `SKILL.md`
gains a "Request governor" section beside the trimmed clone peer-budget
note.

**Contract texts (for the integrator's merge commit, not this branch).**
The six CONTRACT edits drafted on #141 reconciled against what actually
shipped: §1 `--timeout` is a hang detector with governed sleep exempt and
the per-command exemptions gone (login keeps its §10 QR default of 120);
§4 exit 5 gains "a scheduled pass under a partial cooldown exits 0 with a
deferred report"; §9 the journal gains `retry_after`, `request_type`,
`provenance`, `stop_reason`, `governed_sleep_ms`, `request_count`; §5.1
`doctor` gains the cooldown/degraded checks and the JSON sample; the
clone-sync and archive sections replace `SHORT_WAIT`/`WAIT_BUDGET`
descriptions with pacing intervals, the wall-clock cap, and the windowed
breadth budget; the version line bumps. The `account_flood` preview field
is removed from §12's clone-init sample. The exit-5→exit-0 change is a
contract break: ADR-0038 rule 2 argues major, and per ADR-0058 rule 1 the
integrator assigns the version at merge (2.0.0 recommended), with the
CHANGELOG entry naming ADR-0072 and its compare link from `v1.2.25`.

**What the slice shipped, end to end.** Per-request-type cooldowns and a
self-verifying probe (phases 0–3), start-to-start pacing and the rolling
breadth budget (phase 4), the hang-detector deadline and `--max-runtime`
(phase 5), journal/doctor visibility, the scheduled-wake exit-0 contract,
and the retirement of ADR-0045/0052 (phase 6). The ADR's "Evidence from
#140" section still honestly says the numbers carry one live demonstration
and both assumptions remain open — shipping the code does not make them
true.

**Next:** the integrator merges this branch as the release (2.0.0),
landing the CONTRACT/CHANGELOG/version edits above and the compare link;
`docs/ISSUES.md` then closes #145 and unblocks #146 (the job-model map).
