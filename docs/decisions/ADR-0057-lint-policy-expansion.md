# ADR-0057: Ruff lint policy expands to E/W/F/I/UP/C4

Date: 2026-07-26
Status: accepted

## Context

ADR-0027 put `ruff check`, `ruff format --check`, and `pyright` in the gate.
The rule selection stayed at ruff's pre-0.16 default, `E4/E7/E9/F`, with a
note in `pyproject.toml` saying that adopting anything wider was "a separate
lint policy change, not this deps bump". This is that change.

What the narrow set left unguarded:

- **Line length was declared but not enforced.** `line-length = 88` drove the
  formatter, while `E501` sat outside the selection — so 15 lines had drifted
  past 88 in places the formatter cannot break (strings, comments, docstrings).
- **Import order was whatever each session's editor did.** 70 files were
  unsorted, and clone's imports mixed stdlib, Telethon, and first-party in one
  block. Nothing failed, so every diff was free to reshuffle them again.
- **Idiom drift.** `datetime.timezone.utc` (15 sites) and
  `asyncio.TimeoutError` outlived the Python 3.12 floor this project set in
  ADR-0001.

The measured spread over candidate rule families was 933 findings; most of it
was noise or logic rewrites, not formatting.

## Decision

1. `select = ["E", "W", "F", "I", "UP", "C4"]`. Full pycodestyle (so the
   declared line length is enforced), pyflakes as before, import sorting,
   pyupgrade against the 3.12 floor, and comprehension hygiene.
2. **Deliberately excluded**, each for a stated reason rather than taste:
   - `B`, `SIM`, `PTH` — their fixes rewrite logic (`raise ... from`, boolean
     collapsing, `os.path` → `pathlib`). In maintenance mode a behavior change
     starts from a reproducing test, not from a linter.
   - `ARG` — 696 hits, almost all unused arguments in the Telethon fakes the
     test suite is built from. Real signal would drown.
   - `RUF001/002/003` — ambiguous-unicode, which fires on the Cyrillic strings
     ADR-0050 requires in clone output.
   - `UP040` is ignored inside `UP`: PEP 695 `type X = ...` produces a
     `TypeAliasType`, and `get_args()` on it returns `()`, which breaks the
     read-operation registry test that introspects that union.
3. `combine-as-imports = true`, so `from x import a as b, c as d` stays one
   statement instead of being split into one statement per alias — that split
   is what ruff's isort defaults to, and it reads worse than the clone imports
   it would have rewritten.
4. The one-time cleanup is **layout only**: autofixes for import order and
   pyupgrade, two `dict()` literals in tests, and 15 hand-split long lines
   (strings and docstrings re-wrapped, one test renamed, no message text
   changed). `timezone.utc` → `UTC` and `asyncio.TimeoutError` →
   `TimeoutError` are aliases of the same objects on 3.12.
5. Five `scripts/check-architecture.py` ceilings rise by the exact cost of the
   isort section blanks (+1 to +3 each), annotated with this ADR number. No
   file gained a statement.

## Consequences

- Import order, line length, and 3.12 idiom are now enforced, so they stop
  being review topics and stop appearing as incidental diff noise.
- The one-time cleanup touched 86 files; the full gate is green
  (`1132 passed, 9 skipped`), and the diff contains no behavior change.
- The excluded families remain available: adopting `B` or `PTH` later is its
  own ADR, with its own reproducing tests where a fix changes behavior.
- Ceilings ratcheted up by layout, not logic. The next real growth in those
  five files starts from the new number — a small, one-time loss of headroom,
  taken knowingly.
