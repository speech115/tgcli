# ADR-0008: Raw TL passthrough (`tg api`) with fail-closed safety

Status: superseded in part by [ADR-0010](ADR-0010-raw-api-read-allowlist.md) (2026-07-10)

ADR-0010 supersedes this ADR's phase-2 read-classification rule. The raw
passthrough rationale and future write-path requirements remain in force.

## Context

Requirement: "all Telegram functions, source of truth = Telegram
documentation". The machine-readable form of that documentation is the TL
schema, which ships inside Telethon as `telethon.tl.functions.*` (~450
methods). Wrapping each method in a bespoke command contradicts the YAGNI
rule (AGENTS.md) and the scope-creep risk in PLAN.md. Research (2026-07-06)
confirmed no comparable tool wraps everything either: iyear/tdl covers media
only; b1rd33/tg-cli wraps 62 commands and stops there.

## Decision

One escape hatch instead of 450 wrappers:

```
tg api <Namespace.method> --params '<json>' [--write] [--confirm <method>]
```

- **Construction:** params JSON is mapped recursively to TL constructors
  (`"_"` key selects a constructor by name). Peer-typed fields accept
  `@username` / numeric id strings and are resolved to `InputPeer` via the
  session entity cache. Bytes are base64. Honest size estimate: resolver +
  recursive constructor + serializer + safety classifier ≈ 300–500 LOC.
- **Output:** `result.to_dict()` under CONTRACT.md §7. Its shape mirrors the
  Telegram TL layer and is exempt from tgcli's own field-stability rules.
- **Safety, fail-closed, classified in `safety.py`:**
  - read allowlist by method verb: `get*`, `search*`, `check*`, `resolve*`
    run freely. Nothing else is assumed safe (`export*` looks like a read
    but `messages.exportChatInvite` mutates — hence verb allowlist, not
    heuristics).
  - every non-allowlisted method is a mutation: requires `--write`, is
    blocked by `--readonly` / `TGCLI_READONLY` / `TGCLI_NO_SEND` (exit 2),
    and always appends to `audit.jsonl`.
  - destructive verbs (`delete*`, `reset*`, `leave*`, `block*`,
    `edit*Admin*`, `edit*Banned*`) additionally require typed
    `--confirm <Namespace.method>` matching exactly (idea borrowed from
    b1rd33/tg-cli).
  - hard denylist, never callable via passthrough: `account.deleteAccount`,
    `auth.logOut`, `auth.resetAuthorizations`, `account.resetAuthorization`.
    Session lifecycle belongs to `tg accounts`, not to an agent's raw call.
- **Phasing (dependency, not accident):** read-only passthrough lands in
  phase 2. `--write` stays hard-disabled (exit 2 with a "wait for phase 4"
  message) until `safety.py` + audit log exist in phase 4.
- **Out of scope for passthrough:** file-upload TL flows
  (`InputFile` / `upload.saveFilePart` chains) — impractical over JSON;
  wrapped commands (`tg media`, `tg send --file`) own that path.
- **Layer discipline:** Telethon is version-pinned in pyproject.toml.
  Upgrading it is a deliberate commit that re-runs
  `scripts/check-coverage.py` against `docs/FEATURES.md`.

## Consequences

- "Full API coverage" becomes checkable: wrapped commands for daily agent
  use + `tg api` for the long tail + FEATURES.md declaring per-namespace
  status, including explicit exclusions (secret chats — not implemented by
  Telethon; voice/video calls — separate media stack; account signup — ToS
  risk; Bot API — non-goal per PLAN.md).
- `tg send` remains the recommended write path (preview→commit, ADR-0005);
  SKILL.md (phase 6) must direct agents to wrapped commands first and
  `tg api` as last resort. `tg api --write` is the audited power tool, not
  a bypass: it obeys the same env kill-switches.
- An agent cannot destroy the account or the session through the
  passthrough even with `--write`.
