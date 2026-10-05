---
name: verify
description: Prove a tgcli change works against real Telegram, read-only. Use after changing what a command does and before saying it works, or when asked to check, verify, or smoke-test tg on a real account.
---

# Verify a change on real Telegram

Green tests prove the code matches the fakes. This proves it matches Telegram.
Every step runs under `TGCLI_READONLY=1`, so nothing is sent, edited, or
deleted.

1. Run the gate and stop on red:

   ```bash
   ./scripts/gate.sh
   ```

2. Run the live smoke. `TGCLI_LIVE_ACCOUNT` picks the alias (default `main`).
   The draft test skips; it writes, and runs only with `TGCLI_LIVE_WRITES=1`
   when the owner asks.

   ```bash
   TGCLI_LIVE_SMOKE=1 uv run pytest tests/live -q
   ```

3. Run the command you changed, the way a user would:

   ```bash
   TGCLI_READONLY=1 uv run tg --account main --json <command> <args>; echo "exit=$?"
   ```

   - The exit code matches `docs/CONTRACT.md` §4.
   - stdout is exactly one JSON document: pipe it to `python3 -m json.tool`.
   - The fields you changed hold the values you expect.
   - stderr holds diagnostics only; with `--json`, its last line is the error
     envelope on failure.

   For a write command, stop at `--preview` and inspect the preview JSON. A
   commit needs the owner's request in this session, and under
   `TGCLI_READONLY=1` it exits 2 anyway.

4. Report what you ran and what came back: commands, exit codes, and the JSON
   keys and values that prove the change. Don't paste message text, names, or
   phone numbers from a real account into chat or a PR. Show keys, counts, and
   ids instead.

## When it can't run

- **Exit 3:** no authorized session for the alias, or the session is busy
  because another `tg` holds its lock. Say which,
  and stop. Never run `accounts login` yourself.
- **Exit 5:** FloodWait. Report `retry_after` and don't retry in a loop.

Say plainly which step you could not run. A green gate alone doesn't prove a
change in Telegram behavior works.

## Keep it sharp

When a verification needs a step this file lacks, add it here in the same PR.
When you run the same live check twice, move it into `tests/live/`.
