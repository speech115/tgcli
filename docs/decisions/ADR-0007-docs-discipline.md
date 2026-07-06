# ADR-0007: MAP + ADR + DEVLOG as mandatory agent workflow

Status: accepted (2026-07-06)

## Context
This project is developed primarily by AI agents across many sessions.
Context evaporates between sessions; the old stack accumulated architecture
nobody could explain afterwards. The user explicitly requested: a project
map created upfront, and md documentation of every decision agents make.

## Decision
Three living documents, enforced by AGENTS.md:
1. `docs/MAP.md` — the tree with per-module ownership; must match reality
   in every commit that changes structure.
2. `docs/decisions/ADR-NNNN-*.md` — every architectural decision (new dep,
   module, contract or safety change). Supersede, never rewrite history.
3. `docs/DEVLOG.md` — one entry per working session: Did/Decided/Learned/Next.

## Consequences
- A fresh agent session reaches working context from 3 files.
- Decision archaeology ("why is it like this?") has a deterministic answer.
- Slight per-session overhead — accepted as the price of multi-agent work.
