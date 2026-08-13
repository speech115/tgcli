---
id: T28
priority: Debt
labels: [enhancement, needs-triage]
slice: Clone
title: "Split commands/clone.py send execution out of the command module"
---

## Problem

`commands/clone.py` is 1286 lines at the architecture ceiling. RPC send
orchestration (`_forward_batch`, `_reupload_batch`) lives in the command
module while `reupload.py` only owns bytes.

## Expected

Move send execution to `clone/send.py` (or extend reupload). Commands
keep thin sync glue. Double `replies.target` / author pick duplication
are good follow-ons in the same campaign.

## Evidence

- Thermos Wave 4 quality P0
- `scripts/check-architecture.py` ceiling for clone.py

## Acceptance

Owner request. File under ceiling with tests green. Prefer before new
clone features.
