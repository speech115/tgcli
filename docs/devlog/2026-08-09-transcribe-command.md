## 2026-08-09 — `tg transcribe` command (ADR-0075)

**Did:** added `tg transcribe <chat> <message_id>`: resolves the peer,
refuses non-voice messages (exit 4), calls `messages.transcribeAudio` and
waits for the async `updateTranscribedAudio` result bounded by `--timeout`
(default 120 s; expiry is the normal `TIMEOUT` with the `transcription_id`
reported). The result handler registers **before** the request (race-free),
filters on peer+message, and unregisters in `finally`. `PREMIUM_ACCOUNT_REQUIRED`
maps to `PolicyError` (exit 2). New module `commands/transcribe.py`, wired
into parser/dispatch/`_default_timeout`; CONTRACT §1/§5, README, SKILL.md,
FEATURES.md, and a new guide page document it. Issue #97 part 3 (`voice_played`)
was already shipped in `read.py`; part 2 (auto-transcribe in read) stays out
of scope per the ADR. ADR-0076 (story media, #156) was authored alongside —
its implementation lands as the next slice. Boundary test asserts the exact
`TranscribeAudioRequest` with an `InputPeerChannel`; the pending flow is
tested on one event loop via a new FakeClient event-handler seam.

**Decided:** 5 focused tests, then gate green (1748 passed, 9 skipped).

**Learned:** `client(...)` lookup uses the *class* `__call__`, so monkeypatching
an instance attribute cannot stub it; `loop.create_task` takes a coroutine,
not a `gather` future. The docs-gate mirror test hardcodes MAP counts and
must move with them.

**Next:** story media download slice (ADR-0076) — parse `/s/<id>`, resolve via
`stories.getStoriesByID`, `--codec` selection from `alt_documents`.
