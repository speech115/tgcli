# ADR-0113: Runtime errors redact phone-shaped text

Date: 2026-08-13
Status: accepted

## Context

Released commands can surface untranslated exception text in CLI error
envelopes, batch results, persisted job errors, and opt-in verbose tracebacks.
ADR-0042 masks known login phone fields, but Telegram and network exceptions
can echo a phone in either compact (`+12025550123`) or formatted
(`+1 (202) 555-0123`) form. The first runtime scrub covered only compact
numbers and ran after the verbose traceback was printed, leaving two stderr
leaks. This is a full-lane correction under ADR-0073 because it changes
released diagnostics and persisted job error content.

## Decision

- Free-form runtime error text is scrubbed through the shared
  `mask_phones_in_text` helper before it is emitted or persisted by the CLI,
  batch runner, or job runner.
- A phone-shaped substring starts with `+`, contains at least four digits, and
  may contain spaces, tabs, parentheses, periods, or hyphens between digits.
  Bare numeric identifiers remain unchanged.
- `--verbose` retains the traceback required by ADR-0012, but tgcli formats the
  traceback, redacts it, and only then writes it to stderr.

## Rejected alternatives

- Redacting only the final error envelope: the earlier verbose traceback still
  leaks the same exception text.
- Matching only contiguous digits: common display formatting bypasses it.
- Suppressing verbose tracebacks: that removes the diagnostics ADR-0012
  intentionally provides.
- Scrubbing bare digit sequences: that would destroy useful message and peer
  identifiers that are not phone-shaped.

## Contract impact

No CLI flag, JSON shape, or exit code changes. Error message text is
privacy-hardened, so `docs/CONTRACT.md`, version files, and `CHANGELOG.md`
remain unchanged.
