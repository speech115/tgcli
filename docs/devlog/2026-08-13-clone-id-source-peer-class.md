## 2026-08-13 — Clone id includes source peer class (Cursor)
**Did:** implemented thermos T05 and ADR-0100 on
`cursor/clone-id-kind-1ec8`. `clone.state.clone_id` now hashes the source's
peer class (user/chat/channel), not the bare numeric peer id, so a
User/basic-group/Channel collision on one integer no longer shares a clone
state slot. Added `state.resolve_slot`, which migrates a pre-ADR-0100
(class-blind) slot onto its class-aware id in place — `.db` + WAL/SHM
sidecars renamed, one stderr note — the first time that identity is
resolved again; a legacy slot of a *different* class is left alone. Every
`commands/clone.py` call site that used to compute the bare id before a
`state.load` now goes through `resolve_slot`; `jobs/runner.py`'s clone-sync
progress-token lookup gained the same `source_kind` plumbing via a renamed
`clone.resolve_source_identity`. Updated CONTRACT §11 and the ADR index.
Full gate: 1892 passed, 9 skipped; ruff, format, architecture (grace band),
pyright, coverage, and docs green on this diff.
**Decided:** grouped by Telegram peer class (user/chat/channel), not the
finer broadcast/megagroup/forum split, because a megagroup can legitimately
toggle into a forum in place without becoming a different source — an
existing test already pins that drift as a commit refusal (exit 2), not a
new clone slot.
**Learned:** `origin/main` currently fails `ruff check` on
`scripts/publish-thermos-backlog.py` (two pre-existing E501 lines,
unrelated to this change and outside T05's scope) — the full-repo gate step
is red on main independent of this branch; every file this PR touches
passes lint/format/pyright/tests on its own.
**Next:** thermos T18 (kind-aware `lookup.matches` filter tokens) is a
related, separate ticket that coordinates with this migration but is not
addressed here.
