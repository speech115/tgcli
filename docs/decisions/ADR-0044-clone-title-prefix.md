# ADR-0044: Clone destination title prefix

Date: 2026-07-24
Status: accepted

## Context

Since ADR-0020 the clone destination copies the source profile verbatim:
init renames the tool-created channel to the exact source title and the
tool-created discussion group to the source group's display name
(`src/tgcli/commands/clone.py`, destination rename and
`_init_discussion`). The result is two visually identical entries in the
owner's dialog list — after today's clone of a live channel the owner
could not tell the clone from the original without opening `clone list`.

The owner explicitly requested a visible `[Clone]` marking on cloned
channels. The project is in maintenance mode (ADR-0026): this is a
behavior change, so it ships with this ADR and a scoped plan, not as a
silent fix.

## Decision

1. **Tool-created peers are titled `[Clone] ` + the copied name.**
   - Destination channel/forum: `[Clone] {source_title}`.
   - Discussion group (when `comments: enabled`):
     `[Clone] {display_name(source_group)}`.
2. **One helper seam.** A single function
   `attribution.destination_title(title: str) -> str` produces the derived
   title; both rename sites call it (ADR-0043 discipline: shared seam, not
   a copied literal).
3. **State stays clean.** `CloneState.source_title` keeps the unprefixed
   source title: `clone list` filtering, `source.title` in every JSON
   object, and marker recovery are unchanged. Only the live Telegram title
   of tool-created peers carries the prefix, and therefore
   `destination.title` in init/sync JSON output.
4. **Idempotent re-init.** Init compares the live title against the
   *derived* title and edits only on mismatch — a re-run performs zero
   renames on an already-prefixed clone.
5. **Retroactive application is an ordinary init re-run.** Existing clones
   adopt the prefix via `tg clone init SOURCE`: one `EditTitleRequest`, no
   new peers. The resulting service message in the destination tail is
   already `expected` by tail verification (CONTRACT: "the init title
   change").
6. **Everything else is untouched.** Creation-marker flow (marker title →
   rename), about/avatar copy, topic titles, message content and
   attribution rules (ADR-0016/0021) are unchanged — the prefix is a
   deliberate tool marking on tool-created *peers*, not a fidelity change
   to copied *content*.

## Consequences

- `destination.title` in CONTRACT examples changes to the prefixed form;
  CONTRACT is updated in the same commit as the code (AGENTS.md rule).
- Existing clones keep their old title until their next `clone init`
  re-run — retro-marking is opt-in per clone, never a sync side effect.
- The prefix is a constant, not a flag. A configurable prefix is deferred
  until demonstrated need (economy principle, matching ADR-0024's
  `--no-roster` posture).
- A source whose own title already starts with `[Clone] ` would produce a
  doubled prefix; accepted as-is — the derived title is a pure function of
  the source title, and guessing intent there costs more than it saves.
