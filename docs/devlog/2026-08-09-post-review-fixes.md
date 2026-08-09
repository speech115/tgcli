## 2026-08-09 — post-review fixes: transcription-id matching, photo-story codec

**Did:** Retroactive two-axis review of #160/#161/#162 found two spec
deviations; both fixed with reproducing tests first. (1) `tg transcribe`
(ADR-0075): the update wait matched only `msg_id`, so a concurrent
transcription of the same message — another dialog, or a re-run — could
satisfy it with foreign text. The handler now parks updates that arrive
before the RPC response and, once `result.transcription_id` is known, matches
on it (the id is assigned by the server in the response, so no early update
can be ours; the replay preserves the fast-path race-free property). (2) `tg
media download` (ADR-0076): `--codec` on a photo story silently downloaded
the photo; it now raises the same exit-4 `NOT_FOUND` as any missing encoding,
matching CONTRACT §5.

**Decided:** No CONTRACT text change and no new ADR — both fixes implement
the decision sections of ADR-0075/0076 and match already-published CONTRACT
wording (exit 4 for a missing encoding); no released surface changes shape.

**Learned:** The transcription_id is only known after `transcribeAudio`
returns, which is why the shipped code fell back to msg_id — but the server
sends the update strictly after the reply, so a register-before + replay
design is race-free without dropping the fast-path update. `emit_error`
already renders `err.details` into the JSON envelope, so the timeout
`transcription_id` promise in CONTRACT §5 holds.

**Next:** independent whole-diff review of the fix branch; owner decides on
merge and any 2.0.3 release.
