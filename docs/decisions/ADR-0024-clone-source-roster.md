# ADR-0024: Best-effort source-side participant roster during clone sync

Date: 2026-07-17
Status: accepted
Builds on: ADR-0017 (clone), ADR-0023 (comments; discussion-group source peer,
`comments: "unavailable"` honest-marker precedent).

## Context

The user wants a clone to also record "who is in the chat" — the source's
audience — on the backend, automatically, for every chat type where Telegram
permits it. `tg export subscribers` already lists participants on demand
(`iter_participants` → CSV); this ADR wires the same capability into the clone
backend as an automatic snapshot, not a new manual step.

Telegram hard limits shape what "where possible" means:

- **Broadcast channels**: only admins may list participants. A clone's source
  is typically a channel the user does *not* own, so `iter_participants` raises
  `ChatAdminRequiredError`. Member collection is impossible there — no code can
  change that.
- **Megagroups, discussion (comment) groups, legacy basic groups, dialogs**:
  the participant list is available (large megagroups cap near 10k).

So the roster is honest about the channel almost always being unavailable while
its linked discussion group — the actual comment audience — usually collects.

## Decision

- **Automatic, during `sync`, after both message phases.** Once phase 1
  (posts) and phase 2 (comments) have run, `sync` snapshots participants of the
  **source channel** and, when `comments == "enabled"`, the **source discussion
  group**. It runs last so a roster problem can never cost message-sync
  progress (already persisted).
- **Source side only.** The people are on the source; the destination is
  tool-created and empty. tgcli never adds collected users to any chat (no
  consent, spam/ban risk) — it only records them.
- **Best-effort, honest markers, never aborts the sync.** Per peer the status
  is `"collected"`, `"unavailable"` (Telegram refused: not admin / private /
  forbidden), `"deferred"` (FloodWait — retried next run, partial results
  discarded, and **the main clone cooldown is not set**, so a roster flood
  never blocks the next message sync), or `"none"` (no discussion group). An
  access refusal is a recorded marker, mirroring ADR-0023's
  `comments: "unavailable"`, not a `PolicyError`.
- **Fresh full snapshot each run, written atomically.** The roster is a
  point-in-time membership snapshot, so each `sync` rewrites
  `TGCLI_STATE_DIR/clones/<clone_id>-participants.jsonl` via the same
  temp-file + `os.replace` discipline as clone state. One JSON object per line,
  each tagged with its `peer` (`"source"` / `"discussion"`) and carrying the
  `export subscribers` columns (id, username, first/last name, phone, is_bot).
- **JSON output only.** The `sync` response gains a `participants` object
  (`path` + per-peer `{peer_id, status, count, reason}`). The already-wide
  plain `sync` row is left unchanged; agents read `--json` and the sidecar.

## Consequences

- A clone of a channel you do not own reports `source: unavailable` every run —
  expected, not a bug. Its comment group still collects.
- Re-pulling a large discussion group's members every sync costs API calls and
  can FloodWait (→ `deferred`). A cadence gate or a `--no-roster` opt-out is a
  follow-up added only on demonstrated pain, matching ADR-0023's `--no-comments`
  philosophy — not built speculatively.
- The sidecar is personal data (a membership roster) at the same privacy footing
  as `export subscribers`: the user's own account reading chats it can access,
  stored locally under `TGCLI_STATE_DIR`, never in the repo.
