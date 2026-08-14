# Coding Standards — tgcli

The active, checkable rule list that review enforces. `AGENTS.md` stays the
canonical contract and `docs/CONTRACT.md` stays the versioned law; this file
is the working checklist. When an agent does something wrong, add the smallest
rule that would have prevented it, and the reviewer enforces it from then on.

Rules here are concrete and mechanical, and each carries the *why* — what
silently breaks if it is ignored. If a rule needs a "when", a policy debate,
or an owner decision, it belongs in `AGENTS.md` or an ADR, not here. Do not
restate a rule that is already listed.

## Streams and output

- **stdout carries contract data only.** Progress, warnings, and debug go to
  stderr. *Why:* a stray `print()` or warning on stdout corrupts the single
  JSON document a `--json` caller parses — the whole point of the contract is
  that stdout is machine-readable end to end.

  ```python
  # BAD: pollutes the --json document on stdout
  print(f"fetched {len(dialogs)} dialogs")
  return dialogs

  # GOOD: return data; let cli.py emit, diagnostics via note()
  note(f"fetched {len(dialogs)} dialogs")
  return dialogs
  ```

- `--json` emits exactly one JSON document to stdout; the error envelope is
  mirrored as the **last** line of stderr, never interleaved earlier.
- `commands/*` never print — they return data structures; only `cli.py` emits
  and journals. `output.note()` / `output.warn()` → stderr, never stdout.

## Code shape and ownership

- `errors.py` is the only place exit codes live; use the `TgcliError`
  hierarchy. *Why:* exit codes are contract data (§4 of CONTRACT.md); a
  hardcoded `sys.exit(3)` in a command silently drifts from the documented
  table and from the JSON `error.code`.
- `commands/*` never touch Telethon connection management directly — always
  through `session.client(account)`. *Why:* the context manager owns session
  locks and the governor seam; a command opening its own client bypasses both.
- `parser.py` (grammar), `preflight.py` (allowed before a session opens),
  `dispatch.py` (runs once it is open), `cli.py` (lifecycle, emit, journal) —
  one job each.
- Read commands are reachable only through `read_ops` (ADR-0034); the
  architecture check enforces this on `parser`, `preflight`, and `dispatch`.
- No new module, abstraction, or dependency without an ADR. No code comments
  unless asked.

## State and safety

- **State files read back later are written atomically via
  `tgcli.atomic.replace_text`, never `write_text`.** *Why:* a torn write from
  a crash leaves a half-written file that the next run reads back as corrupt —
  atomic replacement means a reader sees either the old or the new content,
  never a mix. `scripts/check-architecture.py` enforces this mechanically.

  ```python
  # BAD: a crash mid-write corrupts the file the next run reads back
  Path(path).write_text(json.dumps(state))

  # GOOD: replace_text writes a sibling then renames over the target
  atomic.replace_text(path, json.dumps(state))
  ```

- **Audit is fail-closed: the audit record is written before the mutation,
  and an unwritable audit blocks the write.** *Why:* tgcli never performs an
  unaudited authorised write; if the audit append can fail silently, safety
  itself becomes optional.
- Preview files are written mode `0600`.
- No daemons, no state outside `~/.config/tgcli/` and `~/.local/state/tgcli/`.
- Untrusted content (message text, names, filenames) is never interpolated
  into shell commands or file paths without sanitizing.

## Data and contract

- Datetimes are ISO 8601 UTC; ids are integers; JSON changes are additive only
  (no rename / remove / retype). *Why:* renaming or retyping a field is a
  breaking change that needs an ADR and a major bump, not a silent edit.
- Long options are spelled in full; a value beginning with `-` goes after `--`.

## Telethon and session

- Never open a `.session` file with bare `python3` or a system/user-site
  Telethon — use the `tg` entrypoint or `.venv/bin/python`. *Why:* the pinned
  Telethon writes a schema the older interpreter cannot read; an ad-hoc script
  on the wrong runtime crashes with `too many values to unpack`.
- **A new or changed Telegram RPC ships with a boundary test that asserts the
  exact Telethon request type and input types.** *Why:* a permissive fake
  proves nothing about what Telegram will accept — only the exact `functions.*`
  request and `types.*` input peers prove the wire shape is right.

  ```python
  # BAD: permissive mock — the wrong request would still pass
  client.send_message = MagicMock(return_value=SimpleNamespace(id=42))
  run_send(client, "chat", "hi")
  client.send_message.assert_called_once()

  # GOOD: the recorded requests are checked by exact TL type
  assert [type(request) for request in client.requests] == [
      functions.channels.GetFullChannelRequest
  ]
  assert isinstance(request.peer, types.InputPeerChannel)
  ```

## Testing

### Core principle

Tests verify **behavior** through public interfaces, not implementation
details. Code can change entirely; tests break only when behavior changes.
CLI work is verified through arguments, stdout/stderr, JSON, and exit codes —
the same seams a `--json` caller relies on.

### Good vs bad tests

```python
# GOOD: asserts observable CLI behavior through args and stdout
assert main(["dialogs", "--json"]) == 0
result = json.loads(capsys.readouterr().out)
assert result["dialogs"][0]["kind"] == "channel"

# BAD: restates the implementation — the function IS the spec
def test_format_id():
    assert format_id(5) == "5"
```

Red flags: mocking internal collaborators (your own modules), testing private
methods, asserting call counts/order of internal calls, a test that breaks on
refactor without a behavior change, a test name that describes HOW not WHAT.

### Mocking

Mock at **system boundaries** only: the Telegram client, time, and the
filesystem when a real instance is not practical. **Never mock your own
modules or internal collaborators** — if something is hard to test without
mocking internals, redesign the interface. The recorded-request fake in
`tests/` (a client whose `__call__` appends the request and returns a canned
reply) is the boundary seam; it must still be interrogated for the exact TL
request and input types, never just "was called".

### TDD workflow: vertical slices

Do not write all tests first, then all implementation — that verifies imagined
behavior. One test, one implementation, repeat: `RED→GREEN: test1→impl1`,
`RED→GREEN: test2→impl2`. Each test responds to what the previous cycle taught.
Never refactor while RED — get to GREEN first.

## Interface design

- **Prefer deep modules**: a small interface with a deep implementation. A few
  methods with simple params hiding complex logic behind them. Avoid shallow
  modules — large interfaces that pass through to thin implementations.
- **Accept dependencies, don't create them** — pass external dependencies in
  rather than constructing them internally (this is what makes the boundary
  fake in tests possible).
- **Return results, don't produce side effects** — a function returning a value
  is easier to test than one that mutates state or prints.

## Git and language

- Commits are single-line imperative summaries; branches are `claude/<topic>`
  or `codex/<topic>`.
- No commits to `main` without an explicit current-session request.
- Code, comments, docs, commits, and CLI output are English.
- Never commit `.env`, `*.session`, audit logs, downloaded media, or anything
  under `~/.local/state/tgcli/`.
