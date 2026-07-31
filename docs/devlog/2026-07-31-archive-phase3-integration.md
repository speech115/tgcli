## 2026-07-31 — Archive Phase 3 review, merge, release (Fable)

**Did:** independent whole-diff review of PR #112 (Phase 3: private
backfill + changes-based sync). Found one Important — `apply_events`
truncated fetched difference events at `--max-events` while persisting the
advanced cursor, silently and unrecoverably losing archive history (the
cap was even consumed by out-of-scope events) — plus three minors
(channel false-tombstones on peer-less deletes by id collision, reconcile
sampling the same first five peers forever, private cross-module imports).
Cursor shipped review-fix2 to the recommended shape: difference events are
always applied in full before the cursor advances, `--max-events` became
the catch-up *message* budget, peer-less deletes exclude `-100…` peers,
reconcile rotates via `next_offset`. Re-pass clean; gate + CI green;
squash-merged as `45f644b`.

**Integrator duties this entry rides with:** architecture ceilings
ratcheted for the Phase 3 growth (`parser.py` 645, `preflight.py` 353,
`dispatch.py` 315, each annotated) and release `1.2.22` cut (version bump
+ CHANGELOG entry with compare link; tag via the release workflow on
merge).

**Learned:** the truncation bug pattern — "bounded apply of already-paid
network results" — is worth watching for in Phase 4's transcribe queue
too: caps belong on network fetches and CPU batches, never between fetched
data and the cursor/checkpoint that marks it consumed.

**Next:** Phase 4 (media + transcription; Russian voice-note acceptance
check gates backfill; FTS-rebuild-preserves-transcripts fix recorded in
the plan), then Phase 5 search surface, Phase 6 scheduling/release.
