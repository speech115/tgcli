# ADR-0030: Outgoing formatting + custom-emoji harvest

Date: 2026-07-22
Status: accepted

## Context

ADR-0028 deferred explicit entities/formatting control under MSG-001. A real
agent task needed to compose and edit posts with the full тень.exe HTML set
(bold/italic/quote/spoiler/custom emoji) and to harvest custom-emoji document
ids from existing posts for reuse. Under ADR-0026 this is an owner-requested
partial re-entry of MSG-001 — not a new subsystem, but a contract-visible
surface change that needs an ADR.

## Decision

1. **Module.** New `src/tgcli/formatting.py` owns `render(text, fmt) →
   (clean_text, entities|None)`. Formats: `plain` (verbatim, no entities),
   `md` (Telethon Markdown), `html` (Telethon HTML plus `<tg-spoiler>` /
   `<span class="tg-spoiler">`, which Telethon 1.44 silently drops).

2. **CLI.** `tg send` and `tg edit` take `--format {plain,md,html}`.
   - `send` defaults to `md` (preserves historical Telethon client default).
   - `edit` defaults to `plain` (surgical edits stay literal unless asked).
   Format name + raw markup live in the preview payload; entities are
   re-rendered at commit into `formatting_entities`.

3. **Read shape.** Universal message JSON gains additive `custom_emoji`:
   `[{id, emoji, offset, length}]` from `MessageEntityCustomEmoji`, so
   `read`/`search`/`message`/`export` expose harvestable ids for
   `<tg-emoji emoji-id="…">` round-trips.

4. **Out of scope (still MSG-001).** Albums, scheduled sends, react, pin,
   protect-content, and a raw `entities` passthrough remain deferred.

## Consequences

- CONTRACT.md documents `--format` and the `custom_emoji` field in the same
  commit as the code.
- ADR-0028's deferred list is narrowed: entities/formatting for send/edit is
  done; the rest of MSG-001 stays deferred with its existing trigger.
- No new dependency; Telethon's HTML/Markdown parsers are reused.
- Extends the ADR-0028 preview→commit path; does not touch clone.
