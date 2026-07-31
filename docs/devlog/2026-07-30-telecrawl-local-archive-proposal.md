## 2026-07-30 — Record telecrawl local archive/search proposal (Codex)

**Did:** added an unvetted `docs/PROPOSALS.md` row for a telecrawl-shaped
local Telegram archive and FTS5 search layer, tied to the owner's concrete
scenario of frequent cross-chat and selected-channel retrieval. Recorded the
scope, read-only boundary, account/privacy requirements, rebaseline gates,
and proof-first sequencing. No production code or CLI contract changed.

**Decided:** no ADR yet. The earlier generic mirror rejection remains valid as
historical context, while this owner scenario is a new re-entry candidate.
`tgcli` remains the live Telegram control route; any archive is a separate
read-only layer until an owner-approved ADR and scoped plan exist.

**Learned:** telecrawl demonstrates a local SQLite `messages` table plus FTS5
over Telegram Desktop/Postbox imports, with explicit archive/search locality
and backup controls. Its public interface does not establish continuous live
sync or complete remote-history coverage, so both freshness and completeness
must be measured before adopting the shape.

**Next:** owner review of the proposal and, if accepted, a selected-dialog
proof of value before choosing a sidecar or native tgcli store.
