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
  mapped-cross-leg, reachable, unreachable, and reachable-then-rejected.

Two traps in this slice fail quietly, so both get a named test rather than a
review pass.

**Entity offsets are UTF-16, not characters.** Prefixing shifts every entity in
the author's text, and Python string length is the wrong unit. A test written
over ASCII passes while real messages skew. Use source 2374's actual quote,
which contains 😴 — a surrogate pair — and assert exact offsets against a
message carrying an entity after the prefix. `clone/attribution.py` already has
the correct helper; the failure mode is reimplementing it rather than calling
it.

**Two id maps sit next to each other.** `id_map` and `discussion_id_map` both
answer `dest_for` and both hold plausible ids, so resolving a target against
the wrong one yields a reply that points at a real but unrelated message and
looks correct in every output. Source 2374 is the live proof: its
`reply_to_msg_id` 1244 addresses a third channel, while the discussion group
has its own unrelated 1244. Assert that 2374 resolves to a fallback and never
to a mapped destination.

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

**Stop here and get the owner's review before any Telegram mutation.** Slices 0
through 4 land, gates pass, and the branch waits. What follows writes into a
real channel under an account with Telegram rate limits, and it does not
unwind: a sent message advances the cursor and enters the id map, so deleting
it afterwards leaves the state describing a message that no longer exists.

1. Copy the clone's state file aside first —
   `~/.local/state/tgcli/clones/4fa28c42….json` — as the repo has done before
   (`*.pre-repair-*.bak`). It is the only way back from a bad run.
2. Read-only: `tg clone status`.
3. `tg clone sync --limit 1` and stop. This copies source 2374 alone, which is
   the fallback case. Read the destination thread by eye: peer title line,
   quote as a blockquote, author's text unmodified, correct thread placement,
   no attached photo. Confirm the run exited nonzero with exactly one
   `quote_flattened` row.
4. Only then run without `--limit` to take 2378 natively — its quote must point
   at destination 773, not at the source channel — and the rest of the backlog
   through source 2394. Expect exit 0 from here on, since 2374 was the only
   fallback.

A FloodWait at any step persists the clone cooldown and exits 5; wait it out
rather than retrying, and do not clear the cooldown by hand.

Deliberately out of scope: no backfill of earlier `reply_flattened` losses
(Telegram fixes `reply_to` at send time), no `reply_media` carriage, and no
change to poll/Story snapshot behavior.

## Related

The `tg message` peer-dropping defect found while diagnosing this is fixed
here, as slice 0, rather than tracked separately — the clone work is verified
through that command.
