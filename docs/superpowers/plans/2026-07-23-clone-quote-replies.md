# Clone quote replies plan

Decision: ADR-0036. Tickets: not yet filed.

Live state this plan targets: clone `4fa28c42…` of `@sral_v_nastav`. Post leg
complete (`cursor` 791, 768 mappings). Discussion leg wedged at
`discussion_cursor` 2373 with 20 comments held behind source 2374. Second
blocker at source 2378. No state repair is required — the cursor never
advanced, so a plain `tg clone sync` catches up once the code ships.

## Slice 0 — make the read surface tell the truth

Goes first because the later slices are verified by reading the destination by
hand, and the tool used for that currently misreports exactly the relationship
under change.

`tg message <chat> <id>` projects a reply header to a bare `"reply_to": <int>`,
dropping `reply_to_peer_id`. For a cross-chat quote the id belongs to another
chat, so the output invites resolving it against the wrong message — chat
4454061248 has its own unrelated message 1244, which is precisely the trap hit
while diagnosing this work.

- Surface the peer alongside the message id when `reply_to_peer_id` differs
  from the chat being read, instead of emitting a bare int. Same-chat replies
  keep their current shape, so no existing consumer changes.
- Surface `quote_text` presence, likewise dropped today and likewise needed to
  confirm slice 2 by eye.
- `docs/CONTRACT.md`: update the message JSON description to match.
- Tests: a regression case built from source 2374's header asserting that a
  foreign-peer reply is distinguishable from a same-chat reply to the same id.

Separable: this slice touches the read surface only and could ship or be
dropped on its own.

## Slice 1 — classify instead of reject

Turn `clone/replies.py` from a validator that raises into a pure classifier
that names the outcome. It stays synchronous and free of a Telegram client, so
its whole surface remains unit-testable against constructed headers.

- Replace `_signature`'s reject list with a closed classification returned from
  `replies.target`: mapped-in-leg, mapped-cross-leg, foreign-peer, flatten, or
  stop. `reply_from` and `reply_media` stop being reject triggers — they are
  server-rendered output, never inputs to `InputReplyToMessage`.
- Keep the stop verdict for the not-understood only: invalid parent id,
  inconsistent album metadata, unrecognized header type (ADR-0036 §4). The
  existing cross-peer check becomes a classification, not an error.
- Move todo-item, poll-option, ephemeral, scheduled, and non-forum forum
  headers from stop to flatten (ADR-0036 §3).
- `transport.decide` consumes the classification and no longer computes
  `reply_flattened` from a `None` return; resolution moves to slice 2, so this
  slice keeps behavior identical for every header shape that works today.
- Tests: rewrite the reject cases in `tests/test_clone_replies.py` as
  classification assertions; add the two real headers from source 2374 and 2378
  as fixtures. Both currently raise; both must classify.

## Slice 2 — resolve a classification against the world

Add `clone/quotes.py`: the async resolver that turns a classification into
either an `InputReplyToMessage` or a rendered fallback. It owns everything that
needs a client, which is why it cannot live in `replies.py`.

- Mapped target in the same leg: rewrite onto `leg.dest_for(parent)`, keep
  quote text, entities, offset.
- Mapped target in the other leg: resolve through the owning leg's map, then to
  the destination anchor by the path `clone/comments.py::_remap` already walks
  for thread roots. Source 2378 quotes post 789 → destination 773 → its anchor.
- Foreign peer: probe reachability once per peer, cache for the run. Reachable
  → native quote pointed at the original. Unreachable → fallback. A reachable
  send that Telegram rejects degrades to the fallback instead of failing the
  batch (ADR-0036 §1).
- Fallback rendering: peer title line, then the quote as a blockquote, then the
  author's unmodified text. Reuse `clone/attribution.py`'s prefix mechanism so
  UTF-16 entity offsets shift once, by one code path. `reply_media` is dropped.
- Generalize `copy_batch`'s comments-only `remap` hook into a resolution step
  both legs run (ADR-0036 §6), and fold `_remap`'s thread-root rewrite into it
  rather than leaving two anchor walks.
- Tests: resolver unit tests with a faked client for each of mapped-same-leg,
  mapped-cross-leg, reachable, unreachable, and reachable-then-rejected;
  entity-offset assertions on the rendered fallback.

## Slice 3 — report the degradation

- Sync collects `quote_flattened` as `{"id", "peer", "reason"}` rows.
- A run that planted at least one fallback finishes its work, writes the full
  result document, and raises `PartialFailure` carrying the `PolicyError` exit
  code (ADR-0032). Runs that plant nothing exit 0.
- JSON gains `quote_flattened`; the plain row gains `quote_flattened_count`.
- Update `docs/CONTRACT.md` §clone-sync: the reject sentence listing
  reply-from, reply-media, and cross-peer shapes no longer describes behavior.
- Tests: `tests/test_cli_clone_sync.py` — the existing assertion that
  `"reply shape is not supported"` reaches stderr inverts into a completed sync
  with a populated `quote_flattened` and a nonzero exit; a clean run still
  exits 0.

## Slice 4 — gates and docs

- `scripts/check-architecture.py`: register `clone/quotes.py` with a reviewed
  ceiling; lower `commands/clone.py`'s ceiling if the resolution hook leaves it
  smaller.
- ADR index row for 0036 lands with the ADR (already done in this branch).
- `docs/MAP.md` gains `clone/quotes.py`; `docs/DEVLOG.md` gets the session
  entry.
- Full gate: `uv run pytest -q`, ruff lint + format, pyright basic.

## Live verification

Read-only first: `tg clone status` on the wedged clone. Then one real
`tg clone sync` against the live clone, which must copy source 2374 with a
rendered fallback, 2378 with a native quote pointed at destination 773, and the
remaining backlog through source 2394 — then exit nonzero with exactly one
`quote_flattened` row. Confirm placement by reading the destination thread.

Deliberately out of scope: no backfill of earlier `reply_flattened` losses
(Telegram fixes `reply_to` at send time), no `reply_media` carriage, and no
change to poll/Story snapshot behavior.

## Related

The `tg message` peer-dropping defect found while diagnosing this is fixed
here, as slice 0, rather than tracked separately — the clone work is verified
through that command.
