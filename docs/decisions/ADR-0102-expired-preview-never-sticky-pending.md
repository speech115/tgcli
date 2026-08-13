# ADR-0102: An expired preview never becomes sticky-pending

Date: 2026-08-13
Status: accepted
Closes: thermos T13

## Context

`begin_commit` (`src/tgcli/safety.py`) validated a preview's kind, renamed
its `.json` file to `.pending`, and only then checked the five-minute TTL.
An already-expired preview was renamed before the check could reject it, so
the file that lands on disk when `begin_commit` raises `"preview has
expired"` was `.pending`, not `.json`.

`store cleanup` skips `.pending` by default (ADR-0028 protects it: the file
carries the `random_id` a retried send needs for Telegram-side
deduplication). It is only eligible for removal with `--include-pending`,
and only once `expires_at + PREVIEW_TTL` has passed — a second full TTL
after the preview's stated expiry, not the plain "past TTL" bar every other
expired preview clears.

The consequence is that a preview whose owning command was already blocked
as expired stays on disk, holding a copy of its payload (chat, text, or
whatever else `create_preview` was given), for up to twice as long as an
identical preview that was simply never begun. Default cleanup — the mode a
user runs without reading the flag list — does not touch it at all. This is
Thermos Wave 1 security Medium#1: PII in a preview outliving its stated TTL
by policy, not by a race.

The same gap exists on retry: `begin_commit` is deliberately re-callable on
a `.pending` preview (a failed network send may be retried with the same
`random_id`, ADR-0028). If that retry lands after the TTL, the existing code
re-validated the TTL against the already-`.pending` file and raised without
ever moving it — same sticky-pending outcome, reached without a fresh
`.json` ever being renamed inside this call.

## Decision

`begin_commit` checks kind and TTL before any rename. A `.json` preview
found expired is rejected in place: it is never touched, so it stays a
plain `.json` and is classified `expired` by `store` the same as any other
never-begun preview.

A `.pending` preview found expired (the retry-after-TTL case) is renamed
back to `.json` before the `PolicyError` is raised. This is the one path
that must still move a file, because the preview was already `.pending`
before this call started; putting it back is how it re-enters the plain
expired bucket instead of staying protected past its due date.

Both branches converge on the same outcome: an expired preview is always a
`.json` file, classified `expired`, reaped by `store cleanup` with no flags
beyond `--confirm`. `--include-pending` and its extra TTL keep their
existing meaning for a preview that is still genuinely in flight — held
`.pending`, not yet past its own `expires_at`.

Kind-mismatch behavior does not change: it was already checked before any
rename in the `.json` branch, and it still short-circuits before the TTL
check in both branches, so a wrong-kind preview keeps landing back exactly
where the existing tests already pin it (`.json` untouched when fresh,
`.pending` untouched when already pending).

## Rejected alternatives

- **Shorten `--include-pending`'s extra TTL wait, or make it the default.**
  Would still leave a plain `store cleanup` (no flags) unable to reap an
  expired-but-began preview, and would touch the `random_id` retry window
  ADR-0028 depends on for every preview, not only the ones that expired
  before their owning command finished.
- **Delete an expired `.pending` file outright inside `begin_commit`.**
  Deletion is `store cleanup`'s job, gated by `--confirm`; `begin_commit` is
  a read/validate path today and should not become a second, uncoordinated
  deletion surface for preview state.
- **Add a `sticky_pending` reason code to `store cleanup` instead.** Adds a
  new classification the scan/cleanup pair has to carry forever for a bug
  that a two-branch fix in `begin_commit` removes at the source.

## Contract impact

`docs/CONTRACT.md`'s `send`/`edit`/`delete`/`forward` commit section now
states that an expired preview never advances to `.pending`, and a
`.pending` retry found expired is moved back to `.json` — either way it is
in the plain expired bucket `store cleanup` reaps by default. `store
cleanup`'s own flags, JSON shape, and `--include-pending` semantics are
unchanged; only which bucket an expired-and-began preview lands in changes.
Because this changes the observable behaviour of a released command
(`begin_commit` backs every `--commit` path), it ships as a patch release.
