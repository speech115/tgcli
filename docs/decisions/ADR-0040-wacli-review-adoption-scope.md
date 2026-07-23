# ADR-0040: wacli-review adoption scope — local-state hygiene and offline diagnostics

Date: 2026-07-23
Status: accepted

## Context

Owner request (2026-07-23): a review of [openclaw/wacli](https://wacli.sh/)
(recorded docs-only in `docs/PROPOSALS.md`, PR #35) surfaced four transferable
items, all sitting behind the ADR-0026 maintenance gate. The owner has now
decided which to adopt. Per ADR-0026 a behavior change needs an explicit owner
request plus an ADR; this ADR is that gate for the two adopted items.

Measured problem (owner's machine, 2026-07-23): **51 of 59** preview files
under the state root are spent yet retained forever; one holds 17 KB of message
text at mode `0644`; `audit.jsonl` is 1.1 MB and unbounded; ~340 KB of relic
directories (`mirrors/`, `mirror-lab/`, `labs/`, `probes/`) remain from the
removed `tg mirror` surface. The state root is `0700`, so nothing leaks
off-account, but stale message bodies with no expiry are the wrong default.

Terminology introduced here (deliberately kept off the `draft` word, which
ADR-0039 already owns for the Telegram-draft surface):

- **preview** — the pre-commit safety record `p_*.json` (ADR-0005), valid for
  a 5-minute TTL.
- **spent preview** — a preview in a terminal file state: `.used` (committed or
  consumed) or `.pending` (mid-commit). A `.used` file carries the message body
  and has zero future value.
- **relic** — a directory left by a removed surface; produced by no current
  code path.
- **audit log** — the append-only mutation trail `audit.jsonl` (ADR-0005).

## Decision

1. **Adopt `tg store stats` / `tg store cleanup`.**
   - `stats` is read-only: reports state-root contents by category, including
     relics flagged "remove by hand".
   - `cleanup` deletes spent previews (`.used`) and expired `.json` (past the
     5-minute TTL, ADR-0005) — the artefacts that carry message bodies and have
     no future value.
   - **Hard boundary (non-negotiable): cleanup never touches the audit log or
     sessions.** See Consequences.
   - `.pending` previews are protected — they hold the ADR-0028 idempotency
     `random_id`; removed only when far past TTL and only under an explicit
     opt-in flag.
   - Live previews within TTL: never.
   - Relics: reported by `stats`, never auto-deleted.
   - Follows the house two-step posture (ADR-0005): report/dry-run by default,
     actual deletion behind an explicit `--confirm`; optional `--older-than N`
     brake to retain recent artefacts.
   - No background or automatic operation (ADR-0002). Offline: touches only
     local files, never Telegram.
   - Also fixes the permission inconsistency: spent-preview bodies are
     world-readable (`0644`) while `audit.jsonl` is `0600`; cleanup (and,
     going forward, preview writes) tighten previews to `0600`.

2. **Adopt `tg doctor --connect` (offline by default).** `doctor` reports
   locally by default — config validity, file permissions, session presence,
   lock state, state size — and performs live authorization/connectivity checks
   only under `--connect`. Today's unconditional live probe fails exactly when
   the session is revoked or the network is down, which is when local
   diagnostics are most needed. Companion to ACCOUNTS-001, whose recovery path
   needs a diagnosis that works on a broken session.

3. **Defer `--events` (NDJSON lifecycle stream) → FEED-001.** The value is real
   — an agent cannot distinguish a live long-running command from a hung one —
   but an event stream is a stability contract (CONTRACT §3), and designing its
   vocabulary in isolation risks a format we later fight. Design it once, with
   FEED-001, where the events surface is the point.

4. **Defer `tg spec`.** The only item that requires overturning a prior accepted
   decision: ADR-0028 rejected it as "a second source of truth that drifts".
   ADR-0034 (grammar-only parser) weakened that objection — a generated spec is
   now a projection of the single source, not a copy — so the topic is
   legitimately reopenable, but on demonstrated drift pain, not preemptively.
   Any re-entry must overturn the ADR-0028 line explicitly.

## Consequences

- The tool gains a way to delete stale local copies of message text that today
  live forever at `0644`. That is the whole privacy motivation, and cleaning
  spent previews fully addresses it.
- **The audit log and sessions stay outside cleanup's reach by design.** The
  audit log is the project's tamper-evident record of every mutation (ADR-0005);
  a cleanup command able to erase it would hand an agent a button to cover its
  tracks, directly against the safety model. Log size is therefore *not*
  addressed here — if 1.1 MB ever becomes a real problem it needs its own
  decision.
- Relics are a one-time manual cleanup, not recurring output; a built-in
  `rm`-equivalent would be scope the problem does not need.
- `doctor` stops failing in the one situation it is most wanted; `--connect`
  preserves the live check for callers who ask.
- The two deferrals are recorded, not dropped: re-entry conditions are explicit
  — FEED-001 for `--events`; demonstrated drift plus overturning ADR-0028 for
  `tg spec`.
- Implementation is not started. Each adopted item lands from a reproducing or
  acceptance test (ADR-0026) and ships as a patch release (ADR-0038).
