## 2026-08-02 — Request governor: review, live acceptance, merge, release 2.0.0 (Opus)

**Did:** reviewed the ADR-0072 implementation slice (#150, phases 3–7,
written by a different model against the executable plans in #149), ran the
live acceptance, merged both, and cut `2.0.0` — the first major, because
exit 5 changes meaning for an identical trigger.

**Review found three blockers, all fixed on the branch before merge.**

1. *The deadline killed the commands the ADR exists to protect.* Phase 5's
   plan said "one number for every command", and the branch implemented it
   faithfully: `export`, `media`, `clone init|sync|refresh` and
   `archive refresh` all inherited the 60 s default, with only deliberate
   governed sleep discounted. Real RPC latency and media transfer are not
   governed sleep, so a 10k-message export or an hourly refresh would have
   died with `TIMEOUT` for doing ordinary work — while `docs/CONTRACT.md`
   still promised those commands no implicit deadline. **The plan was
   wrong, not the implementation.** Resolved by restoring the exemption
   list as data (`_long_running_command`) while keeping the detector itself
   free of command-name branches.
2. *`docs/CONTRACT.md` was not touched at all.* The branch deferred it to
   the integrator, citing ADR-0058 rule 1 — a misreading: that rule says a
   feature branch which *changes* CONTRACT never touches the version files,
   CHANGELOG, or a tag. CONTRACT belongs with the behaviour, and #147 had
   already deferred it specifically to this slice. All six edits landed on
   the branch; only the version line, CHANGELOG, and ceilings waited for
   this entry.
3. *The pacing reservation could dispatch with zero spacing.* The
   loser of a reservation race slept only to the winner's slot and then
   sent immediately, and never recorded its own claim — reproducing exactly
   what the fix it belonged to was written to prevent. Now the loser waits
   to `max(winner, own) + interval` and retries until its claim lands, with
   the ledger's conditional upsert tightened from `<=` to `<` so an
   identical instant is refused rather than overwriting.

The branch's own follow-up sweep then caught that the retry loop introduced
in (3) could spin forever when the ledger became unreadable — and long
commands no longer carry a default deadline, so it would have hung until
Ctrl-C. Fixed to fail open and dispatch, pinned by a test against a real
broken connection. Two missing acceptance tests (G3, G5) were added, and
the residual protocol limit that the fix does *not* close — two processes
that both read "nothing reserved" can dispatch sub-millisecond apart — is
recorded in ADR-0072 rather than left implied.

**Live acceptance on the owner account (read-only, no mutations).**
`doctor` reports the new `governor_cooldowns`/`governor_degraded` checks;
`dialogs --limit 5` returned in 1.8 s with `request_count: 4`; a capped
`archive refresh` (2 dialogs, 20 events, 2 media) ran the full pipeline to
exit 0 in 48.7 s — 476 events applied, 303 inserted, 2 media downloaded, 1
transcription, reconcile 5 sampled with 0 mismatches, no floods. The
journal recorded `governed_sleep_ms: 37414` against `request_count: 39`:
three quarters of that run was the governor deliberately pacing. The
ledger persisted reservations for six request types and 5/100 of the
breadth budget. That number is also the clearest argument for blocker 1 —
a routine hourly pass now legitimately runs ~50 s, and the uniform 60 s
deadline would have been shooting at it.

**Integrator duties this entry rides with:** ceilings ratcheted (`cli.py`
663, `parser.py` 740, `preflight.py` 440, `clone/state.py` 391,
`archive/backfill.py` 320, `archive/refresh.py` 196,
`commands/archive_refresh.py` 118, `commands/archive.py` 546) and the whole
`governor/` package seeded for the first time (`ledger.py` 416, `pacing.py`
231, `gate.py` 186, `registry.py` 133, `probe.py` 86, `seam.py` 66,
`__init__.py` 14) — phases 0–2 landed it in #148 with no ceilings at all,
so it had been growing unguarded. Release `2.0.0` cut. This closes #145 and
unblocks #146.

**Learned — an executable plan is a defect source, not just a defect
filter.** Blocker 1 was not sloppiness; it was an implementer executing a
specified decision that was wrong, and the specification's own trap list
never questioned it. The branch's devlog even wrote down the contract
sentence it was contradicting without noticing the contradiction. The rule
that would have caught it: when a phase deletes a guarantee CONTRACT states
in prose, the phase must quote that sentence and say what replaces it —
before writing the code, not in the phase that reconciles the docs
afterwards.

**Carried forward, deliberately unclosed:** `archive refresh` defers its
whole sync stage when any of its request types is cooling, rather than
doing "the free part" per ADR-0072 decision 4's literal wording;
`resolve_phone.py` keeps its own file-based 3 s gate beside the ledger's;
and the exemption list restored in blocker 1 carries the maintenance cost
the plan wanted to abolish — a new long-running command must remember to
join it, with no test that notices when it doesn't. All three are recorded
here and in the phase devlogs rather than in a backlog nobody rereads.
