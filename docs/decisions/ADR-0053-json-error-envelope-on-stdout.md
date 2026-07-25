# ADR-0053: The `--json` error envelope belongs on stdout

Date: 2026-07-25
Status: accepted

Corrects the implementation of [ADR-0003](ADR-0003-output-contract.md)'s
output contract to match what `docs/CONTRACT.md` §2 already promises. Exit
codes, error codes, and the human-readable mode are unchanged.

## Context

`docs/CONTRACT.md` §2 states two things about a `--json` run: stdout carries
"exactly one JSON document", and "the final error is **also** mirrored to
stderr as a single-line JSON object". "Also mirrored" says the envelope
exists somewhere else first, and the only other stream is stdout.

The code does something different. `emit_error` (`output.py:30-35`) writes
the envelope to `sys.stderr` and nothing to stdout, in both the JSON and the
human branch. A failing `--json` command therefore produces an **empty
stdout**, a JSON line on stderr, and a correct exit code.

This is not theoretical. Driving the `[икона]` catch-up on 2026-07-25, the
resume loop captured stdout to decide whether to continue:

```
out=$(uv run tg --json clone sync 3802378977 2>>$LOG)
case "$out" in *FLOOD_WAIT*) … ;; *) break ;; esac
```

`$out` was empty on a rate-limited exit, so the loop read a FLOOD_WAIT as
success and stopped after one iteration with the clone still unfinished. The
exit code was right and the wrapper ignored it — because the contract had
promised a document on stdout that was never written. Verified directly
afterwards: `tg --json read 999999999` exits 4 with empty stdout and the
envelope on stderr.

Two ways out. Either weaken §2 to say the envelope lives only on stderr, or
make the code honour §2. Weakening it is worse: stdout is the stream a
`--json` consumer is built to read, and telling every caller "parse the
happy path from stdout and the failure path from stderr" is precisely the
asymmetry that produced this bug. The stderr copy still earns its place —
it keeps a human-tailed log complete when stdout is redirected to a file.

## Decision

1. **With `--json`, the error envelope is written to stdout** as the run's
   single JSON document, in the shape already documented:
   `{"error": {"code": …, "message": …, …details}}`.

2. **The stderr mirror stays byte for byte as it is today**, still the last
   line of stderr. Nothing that currently reads it breaks.

3. **The human and `--plain` modes are untouched.** No JSON on stdout in
   human mode; `--plain` failures keep reporting through stderr as they do
   now. A TSV consumer has no envelope to parse and inventing one for it is
   scope creep.

4. **`tg batch` is out of scope.** Its per-operation `{"ok": false, …}`
   lines are its own contract (ADR-0032) and already reach stdout; this ADR
   governs the single top-level envelope only.

5. **Exit codes do not move.** The envelope is additive: code 5 stays 5,
   `retry_after` keeps its meaning, and a caller that only checks `$?` sees
   no change.

6. **CONTRACT §2 gains one clarifying sentence** stating explicitly that the
   envelope appears on stdout and is mirrored to stderr — so the next reader
   does not have to infer it from the word "also". This is a contract change
   and ships as a tagged patch release (ADR-0038).

## Consequences

- A `--json` caller can read one stream for both outcomes, which is what the
  contract always described and what an agent driving the CLI naturally
  assumes.
- Anything currently distinguishing success from failure by "is stdout
  empty" changes behaviour. Inside this repository that pattern exists
  nowhere; outside it, the wrapper that motivated this ADR is the known
  instance, and it was wrong in the direction this change fixes.
- Output is now duplicated on a failing `--json` run. That is deliberate and
  matches §2; the alternative — dropping the stderr copy — would break the
  established habit of reading the tail of a redirected run's stderr.
- The tests must pin both streams: a failing `--json` invocation asserts the
  envelope on stdout *and* the unchanged mirror on stderr, for at least one
  error per exit code, so a future refactor cannot quietly return to
  today's behaviour.
