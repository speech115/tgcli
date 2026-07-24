# ADR-0046: Clone destination ergonomics — mute and the "Clone" folder

Date: 2026-07-24
Status: accepted

## Context

Cloned peers land in the owner's main dialog list with notifications on.
After ADR-0044 they are visually marked (`[Clone] ` title prefix), but they
still ring like real chats and clutter the top of the list on every sync.
The owner asked for clones to arrive muted and grouped into a Telegram
folder named "Clone".

Both are account-level settings — no peer creation, no messages — so their
flood profile is negligible compared to init's peer-creating calls.

## Decision

1. **Init mutes tool-created peers.** After the destination (and, when
   enabled, the discussion group) is ready, `clone init --commit` mutes
   each tool-created peer forever (`account.UpdateNotifySettingsRequest`
   with a far-future `mute_until`). Idempotent: an already-muted peer is
   not touched.
2. **Init files them into the "Clone" folder.** Telegram folders are
   dialog filters: init reads `messages.GetDialogFiltersRequest`, reuses
   an existing filter titled `Clone` or creates one with the lowest free
   id, and adds the tool-created peers to `include_peers` via
   `messages.UpdateDialogFilterRequest`. Idempotent: peers already in the
   filter are not re-added.
3. **Best-effort, honest markers, never an init failure** (ADR-0024
   posture). Telegram limits (10 folders on free accounts, 100
   `include_peers` per folder) or any RPC failure produce a stderr warning
   and a JSON marker, not an error: the clone itself is complete.
4. **Additive JSON.** The init commit response gains
   `"ergonomics": {"muted": <bool>, "folder": "added" | "present" |
   "unavailable"}` summarizing both peers; CONTRACT updated in the same
   commit.
5. **Retro is a re-init.** Existing clones adopt mute + folder via the
   ordinary idempotent `clone init SOURCE` re-run (same as ADR-0044
   titles) — no new peers, two-three cheap setting calls.

## Consequences

- The owner's dialog list stays quiet: clones live in one folder, never
  notify, and remain fully synced.
- Folder membership is the owner's account state: manually removing a peer
  from the folder will be re-added on the next init re-run (not by sync —
  sync never touches ergonomics).
- A free account with 10 folders already gets `folder: "unavailable"` and
  a warning; nothing breaks.
- Two extra RPC per peer at init; nothing at sync time.
