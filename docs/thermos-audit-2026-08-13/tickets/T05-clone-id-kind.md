---
id: T05
priority: P1
labels: [bug, ready-for-agent]
slice: Clone
title: "Clone id must include source peer kind"
---

## Problem

`clone_id(account_user_id, source_peer_id)` hashes bare numeric peer ids.
User / basic group / channel can share the same integer. Two different
sources can collide on one slot → kind mismatch (exit 2) or `--replace`
archives the wrong history.

## Expected

Include source kind (or peer class) in the identity. Existing slots need
a migration / lookup story so live clones are not orphaned.

## Evidence

- `src/tgcli/clone/state.py` (`clone_id`)
- CONTRACT §11 peer kinds
- Thermos Wave 4 security High#2

## Acceptance

1. Test: same numeric id, different kinds → distinct clone ids.
2. Migration path documented and tested for existing on-disk slots.
3. Full gate green. Full-lane if CONTRACT/schema changes (ADR).
