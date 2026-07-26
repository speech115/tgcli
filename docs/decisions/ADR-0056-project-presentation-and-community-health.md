# ADR-0056: MIT license and the standard community-health surface

Date: 2026-07-26
Status: accepted

## Context

The repository is public and documented in depth for the people already
inside it — AGENTS.md, docs/MAP.md, the ADR index, and a 22-page user guide.
The surface a first-time visitor meets was thinner than that:

- **No `LICENSE`.** Public code without a license file is "all rights
  reserved" by default: nobody may legally use, fork, or vendor it, which
  contradicts a README that opens with `git clone`.
- **No `CONTRIBUTING.md` or `SECURITY.md`.** Both bodies of rules exist —
  the working contract in AGENTS.md, the redaction rules scattered across
  AGENTS.md and the guide — but neither lives where GitHub and newcomers
  look for them. For a tool that holds a real Telegram user session, having
  no stated private reporting channel is the sharper gap: the default
  behavior of a finder is to open a public issue with a full reproduction.
- **No issue or pull-request templates.** Every issue arrives shaped by the
  reporter, so maintenance-mode triage (fix vs. feature, ADR needed or not)
  starts by asking the same questions by hand, and nothing warns a reporter
  off pasting session material.
- The README had no build/version signal and no license statement.

## Decision

1. **License: MIT**, `Copyright (c) 2026 speech115`. It matches `telethon`'s
   own license, so the dependency chain stays permissive, and it is the
   lightest license that makes the README's clone-and-run instructions
   legally true. Owner's choice, made in this session.
2. **`CONTRIBUTING.md`** is the human-facing short form of AGENTS.md, not a
   second contract: maintenance-mode posture, setup, the one-command gate,
   the working rules, and a table of documentation duties. AGENTS.md stays
   canonical and wins wherever the two disagree — stated in the file itself.
3. **`SECURITY.md`** names GitHub private vulnerability reporting as the
   channel, lists what must never appear in a report (session files,
   `api_id`/`api_hash`, login codes, phone numbers, real message content,
   raw audit logs), and draws the scope line around what this tool actually
   controls: the safety gates, preview → commit, local-state permissions.
   Telegram platform behavior and `telethon` bugs are explicitly out.
4. **Issue forms** (`bug_report.yml`, `proposal.yml`) and a
   **pull-request template**. Both forms label `needs-triage`, matching
   `docs/agents/triage-labels.md`. The proposal form states the ADR-0026
   gate up front and routes ideas to `docs/PROPOSALS.md`; the PR template
   asks for real `./scripts/gate.sh` output and carries the documentation
   and safety checklists that reviews were running from memory.
5. **README shell:** status badges (CI, release tag, Python, maintenance
   status, license), a contents line, a `Contributing` section, a `License`
   section, and a mermaid diagram of preview → commit next to the existing
   worked example. The banner becomes a `<picture>` pair — the existing dark
   SVG plus a new light one — so it stops fighting a light-theme reader.

## Consequences

- The project is legally usable and forkable; the license is stated in three
  places that agree (LICENSE, badge, README section).
- Security reports have a private path, and the redaction rules are stated
  before a reporter writes rather than after.
- Triage starts from a filled form: version, exact invocation, expected
  behavior, and the rule it breaks.
- Two more documents can drift from AGENTS.md. Mitigation is structural:
  CONTRIBUTING.md summarizes and points, it does not restate rules in its
  own words, and it declares AGENTS.md canonical.
- Badges and issue-form `../blob/main/...` links are the only repository
  content that assumes the GitHub host; the assets themselves stay local
  (no external images), so the README still renders offline.
