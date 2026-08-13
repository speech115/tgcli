# ADR-0101: Batch JSONL fields coerce like CLI argparse, not like Python `bool()`/`int()`

Date: 2026-08-13
Status: accepted (owner request via thermos audit 2026-08-13, ticket
[T15](../thermos-audit-2026-08-13/tickets/T15-batch-jsonl-coercion.md))

## Context

`tg batch` (ADR-0032) shares its typed read operations with the interactive
CLI through `read_ops.from_batch` (ADR-0034). The batch adapter built each
operation's bool/int fields with bare Python `bool()`/`int()`, or with
plain truthiness:

- `bool("false")` is `True` — a mistyped `{"unread_only": "false"}`,
  `{"full": "false"}`, `{"global": "false"}`, or `{"replies": "false"}`
  silently flipped the filter on instead of failing.
- `if p.get("all"):` on `search` treated any truthy JSON value — including
  the string `"false"`, and any non-empty string in general — as global
  search, silently dropping the chat scope the caller intended.
- `int(True) == 1`: a boolean sent where an int was expected (`limit`,
  `depth`, `context`, `message_id`) silently became `1` instead of being
  rejected.
- `read`'s `before_id`, `after_id`, and `topic` had no int coercion or
  validation at all — any JSON type passed through unchecked into the
  Telethon-facing fetch call.

None of this crashed; each case ran real Telegram operations with a
filter or bound the caller never asked for (Thermos Wave 7 security
Medium#2–3). The interactive CLI cannot repeat this class of bug — argparse
only takes filter flags as `action="store_true"`/`store_false"` (no string
form exists to mistype) and `type=int` on a fixed string argv, so "strict
JSON types matching CLI argparse semantics" means: a batch bool field
accepts only a real JSON `true`/`false`, and a batch int field accepts
only a real JSON integer — with `bool` explicitly excluded even though
Python's `bool` is an `int` subclass.

## Decision

1. `read_ops` gains two private coercion helpers used by every `_SPECS`
   batch lambda that has a bool or int field:
   - `_batch_bool(value, field)` returns `False` for a missing key or a
     JSON `null` (every current batch bool flag already defaults to
     `False`), returns the value unchanged for a real `bool`, and raises
     `PolicyError` for anything else.
   - `_batch_int(value, field)` raises `PolicyError` unless `value` is an
     `int` and not a `bool`; `_batch_optional_int(value, field)` is the
     same but treats `None` (missing key or JSON `null`) as "unset" for
     the genuinely optional `read` fields (`before_id`, `after_id`,
     `topic`).
2. `_batch_search`'s `if p.get("all"):` becomes
   `if _batch_bool(p.get("all"), "search.all"):` — the exact bug this
   ticket names.
3. Every affected field's error message follows the existing
   `_batch_choice` convention: `f"batch {field} must be a JSON boolean"` /
   `f"batch {field} must be a JSON integer"`, exit 2 (`BLOCKED`), raised
   from inside `from_batch` before that op's Telethon fetch runs — never a
   generic `RUNTIME` mid-run failure, and never silently coerced.
4. `read`'s `before_id`/`after_id`/`topic` go through `_batch_optional_int`
   for the first time, closing the "some ids skip int validation" gap
   named in the ticket.

## Rejected alternatives

- **Accept a numeric string for int fields** (mirroring `argparse`'s
  `type=int(str)` conversion on shell argv): batch payloads are already
  JSON, which has a native int type; a caller has no shell-quoting reason
  to send `"20"` instead of `20`, and accepting it would keep tolerating
  exactly the type confusion this ticket exists to close.
- **Validate at `tg batch`'s preflight `parse_ops` step, before the
  session opens:** the existing per-op `_batch_choice` validation (unknown
  `kind`/`type`) already runs inside `from_batch`, called from `run_batch`
  before that op's `fetch` — i.e. before any network call for that op, but
  not before earlier ops in the same JSONL have already run. Moving bool/
  int validation to a different stage than the sibling `_batch_choice`
  checks would split one validation concern across two call sites for no
  behavioral gain: `PolicyError` already stops that op's Telegram fetch
  and reports `BLOCKED`, and `--fail-fast` already stops the whole run on
  the first failure when a caller wants that.
- **Reject `float`/`bool`/string but silently coerce nothing:** considered
  and adopted — no partial leniency (e.g. accepting `1.0` as `1`) survived
  review; every non-conforming type is `BLOCKED`.

## Contract impact

`docs/CONTRACT.md` §5.0 gains one paragraph naming the bool/int fields and
their strict-type rule. This changes released `tg batch` behavior: a
payload that used to silently misbehave (`"false"` truthy) or crash as
`RUNTIME` (`int(None)`, `int("abc")`) now fails closed as `BLOCKED` before
that op's Telegram call. No JSON shape, flag, or exit-code taxonomy is
added — `BLOCKED`/exit 2 already existed for `_batch_choice` fields. Patch
release on merge (integrator bumps version / CHANGELOG). Ships with
reproducing red/green tests per affected field in `tests/test_read_ops.py`
and end-to-end `tg batch` regressions in `tests/test_cli_batch.py`.
