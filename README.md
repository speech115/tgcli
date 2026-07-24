# ✈️ tgcli — Telegram CLI: read, search, send

![tgcli banner](docs/assets/readme-banner.svg)

A stateless Telegram client built on [`telethon`](https://github.com/LonamiWebs/Telethon). Signs in as your own user account over MTProto, does exactly one operation per invocation, and gives you JSON-first reading, search, media, export, and preview → commit correspondence from the command line — for humans, scripts, and AI agents alike.

> Third-party tool. Uses the MTProto user API via `telethon`. Not affiliated with Telegram. No daemons, no ports, no LaunchAgents — every command is a foreground process that exits.

Design lineage: [openclaw/gogcli](https://github.com/openclaw/gogcli) (architecture), `tools/telegram` (domain knowledge, sessions, TDLib media experience).

## Features

- **Stateless by design** — one entrypoint, one operation per process. Nothing runs between invocations; state is limited to `~/.config/tgcli/` and `~/.local/state/tgcli/`.
- **Automation contract** — `--json` / `--plain` on stdout, everything human on stderr, documented exit codes, additive-only JSON changes. See [docs/CONTRACT.md](docs/CONTRACT.md).
- **Reading and search** — dialogs with unread/kind filters, id- and date-paginated reads, per-dialog and global search, reply threads, message context windows, contacts, mutual chats, and a read-only JSONL batch mode.
- **Safe correspondence** — `send`, `edit`, `delete`, `forward`, and `draft` all go through preview → commit with single-use ids, a 5-minute TTL, `random_id` retry confirmation, and an append-only audit log.
- **Media and export** — manifest before download, bulk filtered downloads, JSONL message export with `--resume`, and CSV subscriber export for broadcast channels.
- **Chat clone** — copy broadcast channels, non-forum supergroups, and private dialogs with native forwards plus protected-content reupload. See [docs/CLONE.md](docs/CLONE.md).
- **Raw TL escape hatch** — `tg api` reaches the long tail of the pinned Telethon layer behind a default-deny read allowlist, an explicit `--write` gate, typed confirmations for destructive verbs, and a permanent denylist.
- **Diagnostics and hygiene** — `tg doctor` reports locally by default (`--connect` for live checks); `tg store stats` / `tg store cleanup` inspect and reclaim local state without ever touching sessions or the audit log.

## Install

`tgcli` requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/speech115/tgcli.git
cd tgcli
uv sync
uv run tg --help
```

### Put `tg` on your PATH

```bash
./scripts/install-link.sh          # symlinks ~/.local/bin/tg -> .venv/bin/tg
```

Override the target with `TGCLI_BIN_DIR=/somewhere/bin`. The script warns if another `tg` already shadows it on your PATH.

### Configure an account

Create `~/.config/tgcli/config.toml` with credentials from [my.telegram.org](https://my.telegram.org):

```toml
default_account = "main"

[accounts.main]
api_id = 123456
api_hash = "0123456789abcdef0123456789abcdef"
session = "main"          # session file name under ~/.local/state/tgcli/
```

Then verify:

```bash
tg --json doctor --connect
```

Existing sessions from the old `tools/telegram` stack can be adopted with `tg accounts import`.

## Quick start

```bash
# 1. What is waiting for me
tg --json dialogs --unread-only

# 2. Read and search
tg --json read @channel --limit 20
tg --json search --all "invoice" --limit 20

# 3. Send — two steps, always
tg --json send @user "hello" --preview     # returns preview_id
tg --json send --commit p_9f3a             # nothing leaves without this

# 4. Media and export
tg --json media download https://t.me/channel/42 --parallel 4
tg --json export messages @channel --output messages.jsonl --resume

# 5. Health and local state
tg --json doctor
tg --json store stats
```

Chat references accept `@username`, a `t.me/` link, or a numeric dialog id; `tg resolve` additionally takes a phone number in E.164 form. Every command takes `--json` (contract data) or `--plain` (TSV); without either, output is human-readable and unstable by design.

## Documentation

| Area | Pages |
| --- | --- |
| **Contract** | [CLI automation contract](docs/CONTRACT.md) — invocation, streams, exit codes, JSON shapes |
| **Agent usage** | [SKILL.md](SKILL.md) — command routing table, correspondence recipes, safety gates |
| **Coverage** | [feature matrix](docs/FEATURES.md) — every TL namespace with `wrapped` / `api` / `excluded` status |
| **Clone** | [chat clone](docs/CLONE.md) |
| **Decisions** | [ADR index](docs/decisions/README.md) — 40 accepted decisions |
| **Project** | [map](docs/MAP.md) · [scope and re-entry gates](docs/ISSUES.md) · [devlog](docs/DEVLOG.md) · [changelog](CHANGELOG.md) |
| **Contributing** | [AGENTS.md](AGENTS.md) — the behavior contract every agent and human follows here |

## Configuration

Config lives at `~/.config/tgcli/config.toml`; sessions, locks, previews, the audit log, and cache live under `~/.local/state/tgcli/` (mode `0700`). Account selection order is `--account` > `TGCLI_ACCOUNT` > `default_account`.

**Global flags:** `--account NAME`, `--json`, `--plain`, `--readonly`, `--timeout SEC` (default 60; no default deadline for media and exports), `-v/--verbose`, `--version`.

**Environment overrides:**

| Variable | Effect |
| --- | --- |
| `TGCLI_CONFIG` | Config file path. Defaults to `~/.config/tgcli/config.toml`. |
| `TGCLI_STATE_DIR` | State root for sessions, locks, previews, audit log, cache. |
| `TGCLI_ACCOUNT` | Account alias to use when `--account` is absent. |
| `TGCLI_READONLY` | `1` blocks every mutation before any network work. |
| `TGCLI_NO_SEND` | `1` blocks message-producing mutations specifically. |
| `TGCLI_LIVE_SMOKE` | `1` enables the live-account smoke tests in the suite. |

**Exit codes:**

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | runtime error |
| 2 | blocked by safety policy |
| 3 | config or authentication error |
| 4 | not found |
| 5 | rate limited; JSON error carries `retry_after` |

## Preview → commit

Reads are free; every mutation is two invocations. The first one resolves the peer, renders exactly what will be sent, and writes a single-use preview record. The second one commits that record by id — the text is never retyped, so what you reviewed is what goes out.

```bash
tg --json send @channel "<b>bold</b> and a <tg-spoiler>secret</tg-spoiler>" \
  --format html --preview
# → {"preview_id": "p_9f3a", "to": {…}, "text": "…", "expires_at": "…"}

tg --json send --commit p_9f3a
```

- Previews expire after 5 minutes and are consumed on commit.
- A commit that fails on network or runtime error is **retried with the same id** — the stored Telegram `random_id` lets tgcli confirm the original operation instead of duplicating it.
- `edit`, `delete`, `forward`, `draft set/clear`, and `clone init` follow the same rule.
- `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` hard-block mutations with exit 2, before any network call.
- Every committed mutation is appended to the audit log; `tg store cleanup` can never delete it.

## Status

v1.1 in maintenance mode (ADR-0026): feature-complete and in production use. New behavior needs an explicit owner request plus an ADR; a bug fix starts from a reproducing test.

CI runs `pytest`, `ruff`, `pyright`, and a fail-closed TL coverage gate on every push and PR ([.github/workflows/ci.yml](.github/workflows/ci.yml)). `scripts/bench.py` benchmarks every command against a live account (13 steps, ~20 s).

## Credits

- Architecture and CLI posture modelled on [`gogcli`](https://github.com/openclaw/gogcli).
- Local-state hygiene and offline diagnostics adopted from a review of [`wacli`](https://wacli.sh) (ADR-0040).
- Built on [`telethon`](https://github.com/LonamiWebs/Telethon) by LonamiWebs.

## Maintainers

- [@speech115](https://github.com/speech115)
