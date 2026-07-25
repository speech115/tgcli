# ADR-0054: Backfilling body prefixes into an already-copied clone

Date: 2026-07-25
Status: accepted

## Context

A clone is copied once and frozen. `id_map` records source id → destination
id and `clone sync` only ever walks forward from its cursor, so a rendering
improvement shipped after a post was copied never reaches that post. The
clone is whatever the tool was on the day each message went across.

Observed live on `[икона]` in the 2026-07-25 fidelity audit. Five source
posts (54, 69, 73, 78, 81) are reposts: they carry `fwd_from` and Telegram
renders a native "forwarded from" header. Their clones carry neither
`fwd_from` nor the `Переслано от <label>` prefix that
[ADR-0050](ADR-0050-clone-forward-attribution.md) introduced, because all
five were copied on 2026-07-24, hours before ADR-0050 merged. They read as
original statements by the channel — the exact misattribution ADR-0050 was
written to stop. Posts 89–94, copied the next day on 1.2.9, are correct.
One clone, two behaviours, and nothing in the tool can tell them apart:
`CloneState` records the state-schema version (`clone/state.py:12`), never
the tgcli version that did the copying.

Recopying is not the answer. The channel holds four half-gigabyte videos on
a `noforwards=true` source, so every message takes the download+reupload
path; a fresh clone costs hours of transfer and a new destination, and every
`t.me` link into the old one dies.

The project already has a precedent for retroactive repair, and it is
narrow: [ADR-0044](ADR-0044-clone-title-prefix.md) applies the destination
title prefix to existing clones through an ordinary `clone init` re-run that
compares the live title against the derived one and edits only on
divergence. What is missing is the same idea for message bodies.

The rejected alternative is the general one: re-render every copied message
and edit whatever differs. It cannot be made safe. The destination is a real
chat the owner may have edited by hand, and a snapshot body is not a pure
function of its source — an ADR-0048 poll snapshot renders live vote counts,
so re-rendering it produces a different, equally valid string and the
"repair" would rewrite correct history on every run.

## Decision

1. **A new `tg clone refresh SOURCE` subcommand, under preview → commit.**
   It walks `id_map`, decides per post whether a body prefix is missing, and
   edits only those. It is a mutation like any other: `--preview` prints the
   plan and mints a preview id, `--commit p_…` applies it, `safety`'s
   readonly gate blocks it, and each edit writes a `clone-refresh-prefix`
   audit record before the RPC (the `clone-{init,sync}-*` naming already in
   `commands/clone.py`).

2. **Only prefixes, and only onto an untouched body.** A post is eligible
   only when the destination text is **byte identical to the source body
   with no prefix at all**, and the current renderer would produce a prefix
   for it. That single test does two jobs: it proves the copy predates the
   improvement, and it proves nobody has edited the destination by hand.
   Anything else — a body that differs for any other reason — is reported
   and skipped, never guessed at.

3. **Three exclusions, stated in the code and the preview output.** Poll
   snapshots (ADR-0048) are excluded because their body encodes vote counts
   read at copy time. Native re-forwards (ADR-0050 part B) are excluded
   because they carry a real `fwd_from` and have no prefix to add. The
   discussion leg is excluded because comments have always been attributed
   (`quotes._place_thread` forces `as_reuploaded`, so `needs_author` is
   true) — there is nothing to backfill.

4. **The renderer is reused, not reimplemented.** Eligibility and the new
   body both come from `_body_text` / `quote_fallback.apply_body`, which are
   pure and already the single seam both send paths use
   (`commands/clone.py:658`). A second implementation of the prefix rules
   would drift from the first, and the drift would be invisible until it
   wrote to a real channel.

5. **Media is never touched and no message is recreated.** `refresh` issues
   `EditMessageRequest` with text and entities only. Destination ids,
   `id_map`, and both cursors are unchanged, so links keep resolving and a
   later `clone sync` is unaffected.

6. **Flood behaviour is inherited, not invented.** Calls go through
   `_with_cooldown`; a `FloodWaitError` arms the cooldown and exits 5, with
   no retry (ADR-0045). Re-running `refresh` after the cooldown resumes
   naturally: posts already fixed no longer match rule 2 and are skipped.

## Consequences

- The five `[икона]` reposts can be made truthful without recopying the
  channel, and any future prefix rule can reach messages copied before it
  existed.
- Every edited post gains Telegram's "edited" marker. On `[икона]` this is
  invisible — all 60 copied posts were already edited at the source — but on
  a clone of an unedited channel the marker is new information the source
  does not carry. It is the price of the correction and is documented, not
  hidden.
- Rule 2 makes `refresh` idempotent and self-limiting: a second run finds
  nothing, and a destination the owner has edited is left alone. The cost is
  that a clone whose bodies were touched by hand cannot be backfilled at all
  — deliberate, because the alternative is overwriting the owner's words.
- `refresh` reads the source again (one `get_messages` per batch of mapped
  ids) and resolves forward authors (`get_entity`, cached per run), so it
  carries the same flood exposure as a small sync. It is not free and is not
  run automatically.
- The tool still cannot tell which version copied a given message; rule 2
  infers it from content instead. If a future improvement changes a body in
  some way other than a leading prefix, this ADR does not cover it and a new
  decision is required.
