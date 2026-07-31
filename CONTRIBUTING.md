# Contributing to tgcli

Thanks for looking. This page is the short, human version of the rules; the
canonical contract is [AGENTS.md](AGENTS.md) and it wins wherever the two
disagree.

## What lands here

The project is in production use and **owner-gated**
([ADR-0071](docs/decisions/ADR-0071-owner-gated-development.md)): it still
gains features, but the owner decides which ones, before any code exists.

- **Bug fix** — welcome. It starts from a failing test that reproduces the
  bug, then the minimal fix.
- **New behavior** — needs an owner request plus an ADR before any code. An
  unvetted idea goes to [docs/PROPOSALS.md](docs/PROPOSALS.md) via an issue,
  not into a pull request.
- **Docs, tooling, packaging** — welcome as their own pull request, kept
  separate from product behavior.

When in doubt whether something is a fix or a feature, open an issue and ask.

## Setup

```bash
git clone https://github.com/speech115/tgcli.git
cd tgcli
uv sync
uv run tg --help
```

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). No account is
needed to run the test suite; the live smoke tests stay off unless
`TGCLI_LIVE_SMOKE=1` is set with a test account.

Do not open tgcli session files with bare `python3` or a system/user-site
Telethon. Use the `tg` entrypoint or `.venv/bin/python` from this checkout.

## The gate

One command runs exactly what CI runs, in the same order:

```bash
./scripts/gate.sh
```

That is `ruff check` + `ruff format --check`, the architecture check,
`pyright`, `pytest`, the Telethon coverage matrix, and the documentation
drift gate.
Run it before every commit and quote its real output in the pull request —
never "tests pass".

## Working rules

- **TDD.** Failing test → minimal code → green → commit. No production code
  without a test that demanded it.
- **Test at public seams.** CLI behavior is verified through arguments,
  stdout/stderr, JSON, and exit codes. A new or changed Telegram RPC also
  needs a boundary test asserting the exact Telethon request and input types.
- **stdout is sacred.** Only contract data goes to stdout; diagnostics,
  progress, and warnings go to stderr.
- **Stateless.** No daemons, no background processes, no state outside
  `~/.config/tgcli/` and `~/.local/state/tgcli/`.
- **Atomic writes.** State read back later is replaced through
  `tgcli.atomic.replace_text`, never `write_text` — `scripts/check-architecture.py`
  enforces this.
- **Mirror-fix rule.** Fixed a bug class in one subsystem? Grep for sibling
  subsystems sharing the pattern and port the fix plus its regression test in
  the same commit.

## Documentation duties

These are part of the change, not follow-up work:

| Changed | Also update, in the same commit |
| --- | --- |
| CLI flags, JSON shapes, exit codes | [docs/CONTRACT.md](docs/CONTRACT.md) — and ship it as a patch release with a `CHANGELOG.md` section |
| Added, moved, or removed a module | [docs/MAP.md](docs/MAP.md) |
| Public command, global flag, safety summary, or guide page | [README.md](README.md), the affected guide page, and `SKILL.md` where agent routing changes |
| A proposal or deferred issue graduates or ships | its status in [docs/ISSUES.md](docs/ISSUES.md) and [docs/PROPOSALS.md](docs/PROPOSALS.md) |
| An architectural decision | a new `ADR-NNNN-slug.md` in [docs/decisions/](docs/decisions/) plus its index row |
| Anything, at the end of a working session | one `YYYY-MM-DD-slug.md` entry under [docs/devlog/](docs/devlog/) |

`docs/PLAN.md`, `docs/CLONE.md`, `docs/DEVLOG-v1.md`, and everything under
`docs/superpowers/` are **closed history**: read them, never update them.

## Pull requests

- One coherent slice per pull request. Unrelated cleanup, tooling, and product
  behavior belong in separate ones.
- Branch names are `claude/<topic>` or `codex/<topic>`; commits use a
  single-line imperative summary (`Add dialogs command`).
- Review fixes go onto the head of the pull request under review — one branch
  per slice, not per review round.
- Never merge while head-SHA checks are pending or red.

## Never commit

`.env`, `*.session` files, audit logs, downloaded media, or anything from
`~/.local/state/tgcli/`. Sessions and secrets never enter this repository,
including in issue and pull-request bodies — see [SECURITY.md](SECURITY.md).

## Language

Conversation with the maintainer may be in Russian. Everything committed —
code, comments, docs, commit messages, CLI output — is English.
