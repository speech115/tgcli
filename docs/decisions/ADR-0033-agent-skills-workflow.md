# ADR-0033: Agent skills workflow configuration

Date: 2026-07-23
Status: accepted

## Context

The repository already has a canonical agent contract, an ADR corpus under
`docs/decisions/`, and GitHub as its collaboration surface. The installed
engineering flows need explicit repository-local configuration for issue
tracking, triage labels, and domain-document discovery. Their generic defaults
would otherwise introduce a second ADR directory and make cross-session work
depend on implicit machine state.

## Decision

1. GitHub Issues is the issue tracker used by `/triage`, `/to-spec`,
   `/to-tickets`, and `/wayfinder`. Pull requests are not an incoming request
   surface.
2. The canonical triage labels are `needs-triage`, `needs-info`,
   `ready-for-agent`, `ready-for-human`, and `wontfix`.
3. tgcli is a single-context repository. A root `CONTEXT.md` is created lazily
   only when domain modeling produces durable vocabulary.
4. `docs/decisions/` remains the only ADR directory. Engineering skills must
   not create a parallel `docs/adr/` tree.
5. Repository-local routing lives under `docs/agents/`. `AGENTS.md` exposes it
   to every agent; `CLAUDE.md` remains a thin runtime adapter.

## Consequences

- Fresh sessions can discover the tracker and documentation layout without
  relying on user-level skill installation details.
- Existing ADR history remains canonical and unambiguous.
- The setup adds no runtime behavior, dependency, or CLI contract change.
