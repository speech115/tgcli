# Domain Docs

This is a single-context repository.

## Before exploring

Read:

- root `CONTEXT.md`, if it exists;
- relevant accepted decisions under `docs/decisions/`, starting from
  `docs/decisions/README.md`;
- `docs/CONTRACT.md` for CLI behavior.

`docs/decisions/` is the repository's only ADR directory. Do not create a
parallel `docs/adr/` tree.

If `CONTEXT.md` does not exist, proceed silently. `/domain-modeling`,
`/grill-with-docs`, or `/improve-codebase-architecture` may create it lazily
when a domain term actually needs to be defined.

## Vocabulary

Use terms from `CONTEXT.md` when it exists. Do not introduce synonyms for
defined concepts. If a proposed change conflicts with an accepted ADR, surface
the conflict explicitly instead of silently overriding the decision.

