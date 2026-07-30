## 2026-07-30 — Issue #96 runtime boundary and #97 voice playback field

**Did:** started `codex/issue-96-voice-played` from `main` with the existing
telecrawl proposal changes left untouched. Added test-first `voice_played`
projection for universal message JSON and a top-level `doctor.runtime`
fingerprint. Updated the contract, agent/user guidance, guides, and ADRs
0066/0067.

**Decided:** `voice_played` is boolean only for voice messages and `null` for
non-voice or unavailable flags; it is the inverse of Telegram's
`media_unread`. Session files are supported only through `tg` or the checkout
`.venv`; `doctor` reports the active runtime but does not invoke another
interpreter or rewrite sessions.

**Learned:** the repository baseline was green before changes (`1546 passed,
9 skipped`). The local `gh` credential and API access were restored separately;
this code slice remains local until the full gate and publication steps finish.

**Next:** run the full gate, perform the whole-diff review, then commit and
push the feature branch. Integrator must apply the patch release bookkeeping
for the CONTRACT change when merging.
