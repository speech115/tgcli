## 2026-08-09 — transcribe readonly gate and audit (ADR-0079)

**Did:** The independent review of #160 found `tg transcribe` bypassed the
`--readonly` guarantee (CONTRACT §1) and wrote no audit record, while `tg
api` classifies the same method as a write. ADR-0079 classifies
`messages.transcribeAudio` as a server-side mutation (Premium quota; the
transcript is visible to other clients): preflight now blocks `--readonly`
(exit 2, before any network work) and every call appends an audit record with
`chat` + `message_id` (never the text), mirroring `mark-read`. Guide and
CONTRACT §5 updated; the guide's dialog-id example fixed to the actual
positive `entity.id` (same drift corrected for CONTRACT in the post-review
slice).

**Decided:** Wrapper and raw surface agree on the write classification;
audit happens at attempt time, not success time (the quota is consumed by the
attempt itself).

**Learned:** A safety guarantee is only as strong as the classification of
every command against it; a new command whose RPC is already classified as a
write elsewhere should inherit that classification by default.

**Next:** independent whole-diff review; 2.0.3 release with ADR-0078/0079.
