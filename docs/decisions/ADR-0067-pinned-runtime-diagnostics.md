# ADR-0067: Pin session access to the tgcli runtime and report its identity

Date: 2026-07-30
Status: accepted

## Context

Issue #96 documents repeated failures when a system or user-site Telethon 1.42
opens a SQLite session written by tgcli's pinned Telethon 1.44. The error is
outside the `tg` runtime itself, but ad-hoc scripts can still damage the
workflow by opening the same session with an incompatible schema. The project
already installs a pinned `.venv` and has an offline `doctor` command, so the
lowest-risk repair is to make the runtime boundary explicit and make the
active interpreter observable.

## Decision

1. The supported way to open a tgcli session is the `tg` entrypoint or
   `.venv/bin/python` from the tgcli checkout. Bare `python3` and
   system/user-site Telethon are unsupported for opening tgcli session files.
2. `tg doctor` reports a top-level additive `runtime` object containing the
   executable path, Python version, and imported Telethon version.
3. The diagnostic is offline and informational. It does not execute another
   interpreter, rewrite sessions, auto-upgrade Telethon, or change health
   status and exit codes.

## Rejected alternatives

- Upgrade the system/user-site Telethon — outside this repository and can
  create another session compatibility boundary.
- Auto-migrate or rewrite sessions — unsafe without a deliberate backup and
  versioned migration contract.
- Execute `python3` from `doctor` to compare installations — PATH execution is
  environment-dependent and adds a command-execution surface to an offline
  diagnostic; the supported runtime rule is clearer and safer.
- Add a `tg python` command — duplicates the existing `.venv/bin/python` and
  `uv run` entrypoints without solving arbitrary helper-script behavior.

## Contract impact

The `doctor --json` top-level object gains additive `runtime` metadata. Existing
account checks, `ok` calculation, plain columns, exit codes, and Telegram
network behavior are unchanged. README, AGENTS, CONTRIBUTING, SKILL, and the
install/accounts/doctor guides state the runtime boundary.
