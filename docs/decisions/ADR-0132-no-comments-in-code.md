# ADR-0132: No comments in code

Status: Accepted

## Context

The tree carried about 870 comments in Python alone (787 own-line comment
lines and 87 trailing comments). Reviewed one by one, they fell into four
kinds:

- tautologies that repeat what the next line already says;
- outdated notes about code or behaviour that has since changed;
- excuses that let a workaround stand (a `type: ignore` with a reason, a
  private Telethon attribute, a legacy-state branch) and invite the next
  agent to copy it;
- decision records: the reason behind a non-obvious choice.

Comments drift because nothing checks them, and agents treat them as
instructions.

## Decision

Code carries no comments.

- Tautologies and outdated comments are deleted.
- Excuses are deleted and the workaround they justified becomes a GitHub
  issue labelled `comment-debt`.
- Decision records move to an ADR in this directory, grouped by subsystem
  (ADR-0127 to ADR-0131) under the module and symbol they explained.
- Docstrings stay: they are the API description, not commentary.
- Tool directives stay because tools read them: `type: ignore[...]`,
  `noqa: CODE`, `pragma: no cover`, `pyright: ignore[...]`, and a first-line
  shebang. A directive carries no explanation after it.

`scripts/check-comments.py` (in the gate and CI) enforces this as a ratchet
over Python, shell, `.githooks/`, TOML and YAML files. `scripts/
comment-baseline.json` records how many comments each file may still hold. A
file may not gain a comment; a file that lost some must lower its entry with
`--update`, so the baseline only shrinks. The repeatable cleanup is the
`/delete-comment` skill in `.claude/skills/delete-comment/`.

## Consequences

A reason worth keeping is written in an ADR, a docstring, a test name or a
better identifier. The remaining baseline is the configuration files
(`pyproject.toml`, `.github/workflows/ci.yml`, `.coderabbit.yaml`); a later
pass moves those into ADRs and lowers the baseline toward zero.
