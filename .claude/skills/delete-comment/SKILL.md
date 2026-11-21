---
name: delete-comment
description: Review code comments in tgcli (the whole tree or a path), delete them, move decisions to ADRs, file issues for workarounds, and ratchet the comment baseline down. Use when asked to remove or clean up comments, or when check-comments fails.
---

# Delete comments

ADR-0132: code carries no comments. `scripts/check-comments.py` holds the
ratchet; `scripts/comment-baseline.json` lists what is still allowed.

1. List what is left, optionally for one path:

   ```bash
   uv run python scripts/check-comments.py --list | grep '^src/tgcli/clone/'
   ```

2. Read each comment with the code around it and classify it:
   - **Tautology**: repeats what the code says. Delete it.
   - **Outdated**: describes code or behaviour that is no longer true. Delete it.
   - **Excuse**: justifies a workaround (`type: ignore` with a reason, a
     private library attribute, a legacy branch, a broad `except`). Delete the
     comment, keep the code, and file one GitHub issue per workaround family:
     `gh issue create --label comment-debt --title ... --body ...` naming each
     `path:line`.
   - **Decision**: the reason for a non-obvious choice. Move it to the ADR for
     its subsystem (ADR-0127 archive, 0128 clone, 0129 client and transfer,
     0130 CLI and commands, 0131 tests, scripts, hooks and CI) under
     `### \`path\`` as `- \`symbol\`: reason`, or a new ADR for a new
     subsystem, listed in `docs/decisions/README.md`.

3. Keep tool directives without the explanation after them: `# type:
   ignore[code]`, `# noqa: CODE`, `# pragma: no cover`, `# pyright:
   ignore[rule]`. Drop a `noqa` for a rule family `pyproject.toml` does not
   select.

4. Clean up and ratchet:

   ```bash
   uv run ruff check --fix . && uv run ruff format .
   uv run python scripts/check-comments.py --update
   ./scripts/gate.sh
   ```

5. Report the counts: own-line comment lines and trailing comments removed,
   ADR entries added, issues filed, and the remaining baseline total.

A new comment fails the gate. The fix is never a baseline bump: write the
reason in an ADR, a docstring, a test name or a clearer identifier.
