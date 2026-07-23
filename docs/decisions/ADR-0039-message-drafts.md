# ADR-0039: Message drafts as a first-class command

Date: 2026-07-23
Status: accepted

## Context

Owner request (2026-07-23): an agent should be able to leave a prepared
reply sitting in a dialog for the human to read and send, instead of
sending on the human's behalf. Today tgcli has no draft surface at all —
`messages.saveDraft` is not in the `tg api` read allowlist either, so a
draft is not reachable even as raw passthrough. The feature is genuinely
new: it appears in neither `docs/ISSUES.md` nor `docs/PROPOSALS.md`.

The pinned Telethon 1.44 exposes what is needed:
`messages.saveDraft(peer, message, no_webpage, reply_to, entities, media)`
and `messages.getAllDrafts`, plus the high-level `client.get_drafts()`
returning `Draft` objects.

The shape of the risk is unusual for this repo. A draft sends nothing, so
the blast radius on the *chat* is zero — but Telegram keeps exactly one
draft per dialog, syncs it to every device, and keeps **no history**. A
blind `saveDraft` therefore destroys whatever half-written text the human
had in that input box, with no undo anywhere in Telegram. The danger of
this feature is not what it publishes; it is what it silently overwrites.

Two mutation tiers already exist: preview→commit (ADR-0028) for anything
that puts content into Telegram, and directly-gated one-shot writes
(`mark-read`, `dialog pin|mute|archive`) for state toggles.

## Decision

1. **Surface: `tg draft set|show|clear|list`.** `set` mirrors `send`'s
   flags — `--format {plain,md,html}` defaulting to `md`, `--reply-to`,
   `--topic` — because diverging defaults between "write a message" and
   "write a draft of a message" is a trap. Excluded from v1: `--file`
   (draft media needs an `InputMedia` upload that is never sent, which
   raises its own orphaned-upload question) and any `draft send`.
2. **No `draft send`, deliberately.** Telethon offers `Draft.send()`, but a
   command that turns a draft into a message duplicates `tg send` while
   bypassing its `random_id` confirmation (ADR-0028) — a weaker path to the
   same effect. The final press stays with the human, which is the entire
   point of the feature.
3. **`set` and `clear` go through preview→commit**, like `edit`. The
   public preview carries `old_text`, the current draft body, while its
   private persisted payload snapshots the complete observable draft state
   (text, reply, topic, formatting entities). Immediately before `saveDraft`,
   commit re-reads and compares that snapshot. This detects a human change
   observed then without widening the public JSON contract; Telegram provides
   no conditional-save/version token, so it cannot close the final read→save
   race. The same handshake, `expected_kind` check, and audit record apply.
   `clear` gets its own preview rather than being a flag on `set`, because
   destroying human text deserves its own confirmed intent — even though at
   the TL level it is just `saveDraft` with an empty message.
4. **`show` and `list` are typed read operations in `read_ops.py`**
   (ADR-0034), which makes them available inside `tg batch` and safe under
   `TGCLI_READONLY` without extra work. A read added outside the registry
   would recreate the duplication that seam removed.
5. **Own JSON object, not `message_to_dict`.** A draft has no id, no
   sender, no permalink, no reactions, and its `date` means "last edited",
   not "sent". Reusing the message shape would emit four permanently-null
   fields and assert that a draft is a message:

   ```json
   {"chat": {"id": 0, "name": ""}, "text": "", "custom_emoji": [],
    "reply_to_msg_id": null, "topic_id": null, "date": null,
    "is_empty": true}
   ```

   `custom_emoji` follows ADR-0030, which already settled how custom emoji
   are surfaced.
6. **Ships as patch `v1.1.1`** per ADR-0038; the minor stays where it is
   until the owner declares a milestone.

## Consequences

- An agent gains a way to propose text without the authority to publish
  it. This is a meaningfully different permission from `send`, and it is
  the first tgcli write whose entire purpose is to *not* reach anyone.
- `TGCLI_READONLY` blocks `set`/`clear` and permits `show`/`list`, with no
  new gate code — the tier choice in decisions 3 and 4 buys that.
- Live acceptance is a merge gate, not a follow-up. Two of the four checks
  cannot be proven by mocks: Telegram answers no-op mutations with an
  error (`*NotModified`, ADR-0023) while mocks stay silent, so a repeated
  `set` with identical text and a `clear` of an already-empty draft must be
  exercised against the real API. `saveDraft` also returns a bare `Bool` —
  the exact result type that crashed `tg api` in the 1.1.0 cycle — so the
  boundary test asserts the request and result types explicitly.
- The excluded `--file` and `draft send` remain cheap to add later; both
  were dropped for scope, not blocked by the design.
