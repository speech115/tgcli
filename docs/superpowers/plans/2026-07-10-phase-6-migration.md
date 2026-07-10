# Phase 6 Migration & Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the four authorized accounts from the old daemon stack into
tgcli (`tg accounts import`), put the new `tg` first on PATH
(`scripts/install-link.sh`), and give agents a usage contract (`SKILL.md`) so
a two-week parallel-use period can start.

**Architecture:** `accounts import` is a pure-local command (no Telethon
connection): it copies session SQLite files from the old stack's
`~/.telegram-mcp*` state dirs via `sqlite3` online backup (safe while the old
daemons still run), and appends missing `[accounts.<alias>]` blocks to
`~/.config/tgcli/config.toml`. `install-link.sh` symlinks the venv
entrypoint into `~/.local/bin` (first PATH dir), shadowing the old
`~/bin/tg` wrapper without touching it. `SKILL.md` follows the gogcli
pattern: wrapped commands first, `tg api` last resort.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `fcntl`, `tomllib`), bash,
pytest.

**Scope update (2026-07-10):** `pl` was retired from the default migration
list after live verification found its old session unauthorized. Default
imports now cover `main`, `recklessou`, and `teamsyncsage`; `pl` remains
explicitly importable after a future reauthorization.

**Cutover update (2026-07-10):** the user accepted tgcli as the operational
base without a parallel-use window. Old MCP daemons remain installed but are
legacy infrastructure rather than a normal fallback; unloading them is a
separate explicit operation.

## Recon Facts (2026-07-10, verified on this machine)

- Old-stack Telethon sessions (one dir per account, file `session.session`):
  `main → ~/.telegram-mcp/`, `pl → ~/.telegram-mcp-pl/`,
  `recklessou → ~/.telegram-mcp-recklessou/`,
  `teamsyncsage → ~/.telegram-mcp-teamsyncsage/`,
  `vermassov → ~/.telegram-mcp-vermassov/` (REVOKED — ADR-0009; not in the
  ADR-0004 import list; importable only by explicit request, will exit 3 at
  use time until reauthorized).
- Per-account `api_id`/`api_hash` live in `<dir>/launchd.env` as
  `TELEGRAM_API_ID=` / `TELEGRAM_API_HASH=` lines.
- `~/.config/tgcli/config.toml` currently defines only `main`;
  `~/.local/state/tgcli/sessions/` has only `main.session` (already warmer
  than the old copy — must NOT be overwritten by default).
- Old wrapper: `~/bin/tg → tools/telegram/mcp/bin/tg`. Resolved PATH order
  puts `~/.local/bin` FIRST and `~/bin` fifth, and `~/.local/bin/tg` does
  not exist → the new symlink target is `~/.local/bin/tg`.
- New entrypoint: `[project.scripts] tg = "tgcli.cli:entrypoint"` →
  `<repo>/.venv/bin/tg`.

## Global Constraints

- No daemons, no background processes (AGENTS.md hard rule).
- State only under `~/.config/tgcli/` and `~/.local/state/tgcli/`
  (honor `TGCLI_CONFIG` and `TGCLI_STATE_DIR` envs in all new code).
- stdout = contract data only; progress/warnings → stderr.
- Never commit `.session` files or secrets; tests use tmp dirs, never real
  `$HOME`.
- TDD: failing test → minimal code → green. `pytest -q` before every commit.
- CONTRACT.md updated in the same commit as any CLI surface change.
- Commit style: single-line imperative, on branch
  `claude/phase-6-orchestration-5ca63b`.

---

### Task 1: `tg accounts import` (pure-local command)

**Files:**
- Create: `tests/test_cli_accounts_import.py`
- Modify: `src/tgcli/commands/accounts.py`
- Modify: `src/tgcli/cli.py` (parser lines ~39-45, dispatch lines ~230-232)
- Modify: `docs/CONTRACT.md` (new §9 "Accounts (phase 6)")

**Interfaces:**
- Consumes: `tgcli.config.default_config_path()`, `tgcli.config.load_config`,
  `tgcli.session.state_dir()`, `tgcli.errors.NotFoundError`,
  `tgcli.errors.ConfigError`, `tgcli.output.warn`.
- Produces:
  `DEFAULT_IMPORT_ALIASES = ("main", "pl", "recklessou", "teamsyncsage")`;
  `def import_accounts(aliases: list[str] | None, source_root: Path,
  force: bool) -> dict` returning
  `{"imported": [{"alias": str, "session": str, "status":
  "imported"|"skipped_existing"|"source_missing", "config":
  "added"|"unchanged"}]}`;
  `def import_rows(data: dict) -> list[tuple]` returning
  `(alias, status, config)` rows.

**Behavior contract (write these as tests):**
- Source dir for alias `main` is `<source_root>/.telegram-mcp`; for any other
  alias `<source_root>/.telegram-mcp-<alias>`. Session file inside:
  `session.session`; credentials file: `launchd.env` with
  `TELEGRAM_API_ID=<int>` / `TELEGRAM_API_HASH=<str>` lines (tolerate
  optional `export ` prefix and blank/comment lines).
- Default invocation (`tg accounts import`) processes
  `DEFAULT_IMPORT_ALIASES`; a missing source dir yields status
  `source_missing` plus a stderr warning, and does not fail the run.
- Explicit invocation (`tg accounts import pl vermassov`) processes only the
  named aliases; a missing source dir for an explicitly named alias raises
  `NotFoundError(f"no old-stack session for {alias!r} at {src_dir}")`
  (exit 4).
- Copy uses sqlite3 online backup, not `shutil.copy`:
  ```python
  src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
  dst = sqlite3.connect(dst_path)
  src.backup(dst)
  ```
  closed in `finally`. This is what makes copying safe while the old daemon
  still has the session open.
- Destination `state_dir()/sessions/<alias>.session`: if it already exists
  and `force` is False → status `skipped_existing`, file untouched (protects
  the already-warm `main.session`). `--force` re-copies.
- While writing the destination, hold the same non-blocking
  `fcntl.flock` on `sessions/<alias>.lock` that `session.client` uses; a
  busy lock raises `ConfigError` (exit 3) — never write under a live tgcli
  process.
- Config update: if `<alias>` is absent from the config's `accounts`, append
  ```toml

  [accounts.<alias>]
  api_id = <int>
  api_hash = "<str>"
  session = "<alias>"
  ```
  to `default_config_path()` (create file with mode 0o600 if missing;
  `chmod 0o600` after append), report `config: "added"`; otherwise
  `config: "unchanged"` and the existing block is never modified.
  A source dir without a parseable `launchd.env` while the alias is missing
  from config → `ConfigError` naming the file (exit 3).
- `import` is dispatched on the local (non-network) path in `cli.py`, like
  `accounts list` — it must not create a TelegramClient and must not require
  a resolvable default account.

- [ ] **Step 1: Write the failing tests**

```python
import json
import sqlite3

import pytest

from tgcli.cli import main


def make_old_stack(root, aliases=("main", "pl"), api_id=12345, api_hash="hash-abc"):
    for alias in aliases:
        d = root / (".telegram-mcp" if alias == "main" else f".telegram-mcp-{alias}")
        d.mkdir(parents=True)
        conn = sqlite3.connect(d / "session.session")
        conn.execute("CREATE TABLE sessions (auth_key BLOB)")
        conn.execute("INSERT INTO sessions VALUES (?)", (alias.encode(),))
        conn.commit()
        conn.close()
        (d / "launchd.env").write_text(
            f"TELEGRAM_API_ID={api_id}\nexport TELEGRAM_API_HASH={api_hash}\n"
        )


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('default_account = "main"\n[accounts.main]\n'
                      'api_id = 1\napi_hash = "existing"\n')
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    old_root = tmp_path / "oldhome"
    make_old_stack(old_root)
    return tmp_path, old_root, config


def test_import_copies_sessions_and_appends_config(import_env, capsys):
    tmp_path, old_root, config = import_env
    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])
    assert code == 0
    report = {e["alias"]: e for e in json.loads(capsys.readouterr().out)["imported"]}
    assert report["pl"]["status"] == "imported"
    assert report["pl"]["config"] == "added"
    copied = sqlite3.connect(tmp_path / "state" / "sessions" / "pl.session")
    assert copied.execute("SELECT auth_key FROM sessions").fetchone() == (b"pl",)
    assert "[accounts.pl]" in config.read_text()
    assert 'api_hash = "existing"' in config.read_text()  # main block untouched


def test_import_skips_existing_session_without_force(import_env, capsys):
    tmp_path, old_root, _ = import_env
    dest = tmp_path / "state" / "sessions"
    dest.mkdir(parents=True)
    (dest / "main.session").write_bytes(b"warm")
    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])
    assert code == 0
    report = {e["alias"]: e for e in json.loads(capsys.readouterr().out)["imported"]}
    assert report["main"]["status"] == "skipped_existing"
    assert (dest / "main.session").read_bytes() == b"warm"


def test_import_default_tolerates_missing_sources(import_env, capsys):
    _, old_root, _ = import_env  # only main+pl exist; recklessou/teamsyncsage absent
    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])
    assert code == 0
    captured = capsys.readouterr()
    report = {e["alias"]: e for e in json.loads(captured.out)["imported"]}
    assert report["recklessou"]["status"] == "source_missing"
    assert "recklessou" in captured.err


def test_import_explicit_missing_alias_exits_4(import_env, capsys):
    _, old_root, _ = import_env
    code = main(["--json", "accounts", "import", "ghost",
                 "--source-root", str(old_root)])
    assert code == 4


def test_import_missing_credentials_for_new_alias_exits_3(import_env, capsys):
    _, old_root, _ = import_env
    (old_root / ".telegram-mcp-pl" / "launchd.env").unlink()
    code = main(["--json", "accounts", "import", "pl",
                 "--source-root", str(old_root)])
    assert code == 3
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_cli_accounts_import.py -q`
Expected: FAIL — `accounts import` parser does not exist (argparse error →
exit 1, asserts fail).

- [ ] **Step 3: Implement `import_accounts` in `commands/accounts.py`**

Add to `src/tgcli/commands/accounts.py` (keep `list_accounts`/`to_rows`
as-is): `DEFAULT_IMPORT_ALIASES`, `_source_dir(source_root, alias)`,
`_read_credentials(env_path) -> tuple[int, str]` (parse KEY=VALUE with
optional `export `; raise `ConfigError` if either key is missing or the file
is absent), `_backup_sqlite(src_path, dst_path)` (code in behavior contract),
`_append_config_block(config_path, alias, api_id, api_hash)`, and
`import_accounts(aliases, source_root, force)` composing them per the
behavior contract, plus `import_rows(data)`.

In `cli.py`: add to the accounts subparser
```python
p_import = accounts_sub.add_parser(
    "import", help="Copy authorized sessions from the old stack",
    parents=[global_flags],
)
p_import.add_argument("aliases", nargs="*", metavar="ALIAS")
p_import.add_argument("--source-root", type=Path, default=Path("~"))
p_import.add_argument("--force", action="store_true")
```
and in the local dispatch branch:
```python
if args.command == "accounts":
    if args.subcommand == "import":
        data = accounts_cmd.import_accounts(
            args.aliases or None,
            args.source_root.expanduser(),
            force=args.force,
        )
        rows = accounts_cmd.import_rows(data)
    else:
        data = accounts_cmd.list_accounts(config)
        rows = accounts_cmd.to_rows(data)
```

- [ ] **Step 4: Run GREEN, then the full suite**

Run: `.venv/bin/pytest tests/test_cli_accounts_import.py -q && .venv/bin/pytest -q`
Expected: all PASS (165 existing + 5 new, 8 skipped).

- [ ] **Step 5: Document in CONTRACT.md §9 and commit**

Add §9 "Accounts (phase 6)": invocation
`tg accounts import [ALIAS ...] [--source-root PATH] [--force]`, the JSON
shape from Interfaces, TSV columns `alias,status,config`, the
skip-existing/`--force` rule, exit 4 for explicitly named missing sources,
exit 3 for busy lock or unparseable credentials, and the note that import is
local-only (no Telegram connection).

```bash
git add tests/test_cli_accounts_import.py src/tgcli/commands/accounts.py src/tgcli/cli.py docs/CONTRACT.md
git commit -m "Add tg accounts import from old-stack sessions"
```

### Task 2: `scripts/install-link.sh`

**Files:**
- Create: `scripts/install-link.sh` (mode 755)
- Create: `tests/test_install_link.py`

**Interfaces:**
- Consumes: repo layout `<repo>/.venv/bin/tg`, env overrides
  `TGCLI_BIN_DIR` (default `~/.local/bin`) and `TGCLI_REPO` (default: the
  script's parent repo).
- Produces: idempotent symlink `<bin_dir>/tg → <repo>/.venv/bin/tg`; prints
  the created link and any still-shadowing wrapper to stderr; exits 1 if the
  venv entrypoint is missing.

- [ ] **Step 1: Write the failing tests**

```python
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-link.sh"


def run_script(env):
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True,
                          text=True, env={**os.environ, **env})


def make_fake_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    entry = repo / ".venv" / "bin" / "tg"
    entry.write_text("#!/bin/sh\necho tgcli\n")
    entry.chmod(0o755)
    return repo


def test_creates_symlink_to_venv_entrypoint(tmp_path):
    repo = make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    result = run_script({"TGCLI_BIN_DIR": str(bin_dir), "TGCLI_REPO": str(repo)})
    assert result.returncode == 0, result.stderr
    link = bin_dir / "tg"
    assert link.is_symlink()
    assert link.resolve() == (repo / ".venv" / "bin" / "tg").resolve()


def test_idempotent_rerun_keeps_link(tmp_path):
    repo = make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    env = {"TGCLI_BIN_DIR": str(bin_dir), "TGCLI_REPO": str(repo)}
    assert run_script(env).returncode == 0
    assert run_script(env).returncode == 0
    assert (bin_dir / "tg").is_symlink()


def test_fails_without_venv_entrypoint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = run_script({"TGCLI_BIN_DIR": str(tmp_path / "bin"),
                         "TGCLI_REPO": str(repo)})
    assert result.returncode == 1
    assert "uv sync" in result.stderr
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_install_link.py -q`
Expected: FAIL — script does not exist.

- [ ] **Step 3: Write the script**

```bash
#!/usr/bin/env bash
# Symlink the tgcli entrypoint onto PATH ahead of the old tools/telegram wrapper.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${TGCLI_REPO:-$(dirname "$script_dir")}"
bin_dir="${TGCLI_BIN_DIR:-$HOME/.local/bin}"
entry="$repo/.venv/bin/tg"

if [[ ! -x "$entry" ]]; then
  echo "error: $entry not found or not executable; run 'uv sync' in $repo first" >&2
  exit 1
fi

mkdir -p "$bin_dir"
ln -sfn "$entry" "$bin_dir/tg"
echo "linked $bin_dir/tg -> $entry" >&2

resolved="$(command -v tg || true)"
if [[ -n "$resolved" && "$resolved" != "$bin_dir/tg" ]]; then
  echo "warning: 'tg' currently resolves to $resolved; ensure $bin_dir precedes it in PATH" >&2
fi
```

Run: `chmod 755 scripts/install-link.sh`

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest tests/test_install_link.py -q && .venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/install-link.sh tests/test_install_link.py
git commit -m "Add install-link.sh PATH cutover script"
```

### Task 3: `SKILL.md` (agent usage contract, gogcli pattern)

**Files:**
- Create: `SKILL.md` (repo root)

**Interfaces:**
- Consumes: CONTRACT.md §1–§9 (flags, exit codes, JSON shapes), PLAN.md
  phase-6 acceptance ("direct agents to wrapped commands first, `tg api`
  last resort").
- Produces: a self-contained skill document with frontmatter
  `name: tgcli` and a routing description.

- [ ] **Step 1: Write SKILL.md** with exactly these sections:

1. **Frontmatter** — `name: tgcli`, `description:` one paragraph: stateless
   Telegram CLI for reading dialogs, searching, media download, safe sends,
   exports; use for any live Telegram task instead of the old MCP daemons.
2. **Golden rules** — always `--json` for machine use; stdout is data,
   stderr is progress; check exit codes (table copied from CONTRACT §4);
   one process per account at a time (exit 3 = busy lock, retry in seconds);
   never parse human output.
3. **Command routing table** — task → wrapped command, covering: list
   accounts, list dialogs, read/search/latest/message/info/count, media
   download (incl. `--parallel`), send preview→commit (two-step, single-use,
   5-min expiry), export messages/subscribers, accounts import. Each row:
   one example invocation.
4. **`tg api` — last resort** — only when no wrapped command covers the
   task; read allowlist is default-deny (ADR-0010); writes need `--write`
   (+ typed `--confirm` for destructive verbs); permanent denylist; audit.
   State explicitly: "prefer a wrapped command whenever one exists".
5. **Safety gates** — `--readonly`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`;
   exit 2 semantics.
6. **Account selection** — `--account` > `TGCLI_ACCOUNT` > config default;
   available aliases: main, pl, recklessou, teamsyncsage.
7. **Migration note** — old `tools/telegram` MCP daemons remain available
   during the parallel period; report regressions rather than silently
   falling back.

- [ ] **Step 2: Verify every example against the real parser**

For each documented invocation, run a parse check, e.g.:
```
.venv/bin/python -c "from tgcli.cli import build_parser; build_parser().parse_args(['read', '@chan', '--limit', '5'])"
```
Expected: no example uses a flag or subcommand that does not exist.

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "Add SKILL.md agent usage contract"
```

### Task 4: Docs closure, live migration, cutover

**Files:**
- Modify: `docs/MAP.md` (accounts.py → `[done]`, install-link.sh →
  `[done]`, add SKILL.md row)
- Modify: `docs/DEVLOG.md` (session entry)
- Outside repo (orchestrator-only, with explicit user-visible note):
  `~/.claude/CLAUDE.md` Telegram routing paragraph.

- [ ] **Step 1: Update MAP.md rows** in the same style as existing rows.

- [ ] **Step 2: Run the full suite and commit docs**

Run: `.venv/bin/pytest -q`
Expected: all pass.

```bash
git add docs/MAP.md docs/DEVLOG.md
git commit -m "Record phase 6 migration in MAP and DEVLOG"
```

- [ ] **Step 3 (live, orchestrator):** run the real import on this machine:
`tg accounts import --json` → expect `pl`/`recklessou`/`teamsyncsage`
`imported`, `main` `skipped_existing`; then smoke
`tg --account pl dialogs --limit 1 --json` (exit 0).

- [ ] **Step 4 (live, orchestrator):** run `scripts/install-link.sh`;
verify `command -v tg` → `~/.local/bin/tg` and `tg --version` prints 0.1.0.

- [ ] **Step 5 (orchestrator):** update the Telegram section of
`~/.claude/CLAUDE.md`: new `tg` (tgcli) becomes the first route for
CLI-suited live Telegram tasks, old MCP daemons stay as the explicit
fallback during the two-week parallel window. Report the exact diff to the
user. Do NOT unload LaunchAgents — that happens only after the parallel
window (PLAN.md).

**Open decision to surface to the user (not auto-executed):** `vermassov` —
session exists but is revoked (ADR-0009); it held unique private-channel
access. Options: reauthorize it in the old dir and then
`tg accounts import vermassov`, or drop it. Default: not imported.

## Self-Review

- Spec coverage: PLAN phase 6 lists import (Task 1), install-link.sh
  (Task 2), SKILL.md with wrapped-first/api-last (Task 3), CLAUDE.md routing
  update (Task 4 step 5), parallel-period rule (Task 4 note; LaunchAgents
  untouched). The "one normal working week" acceptance is calendar-based
  and starts after this session — recorded in DEVLOG, not automatable.
- Placeholder scan: no TBD/TODO; every code step shows the code.
- Type consistency: `import_accounts(aliases, source_root, force) -> dict`
  matches the cli.py dispatch and the test assertions; statuses
  `imported|skipped_existing|source_missing` used identically in tests,
  contract, and rows.
