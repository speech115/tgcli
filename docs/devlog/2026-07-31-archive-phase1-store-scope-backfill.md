## 2026-07-31 — Archive Phase 1: store, scope, selected-dialog backfill (Cursor Grok)

**Did:** implemented ADR-0068 Phase 1 on `codex/archive-store`. New
`src/tgcli/archive/` package (`store.py` schema v1 + WAL + FTS5 + account
binding, `scope.py` standing private category, `backfill.py` capped history
walk) and `commands/archive.py` surface wired through parser / preflight /
dispatch / cli. Commands: `init`, `add`, `remove`, `list`, `status`,
`backfill CHAT [CHAT …] [--limit N]` (default 100 / hard caps 1000 msgs and
20 dialogs; no empty→all sentinel). Offline `list`/`status` never open a
Telegram session; network commands verify live `get_me().id` against the
bound store (exit 2 on mismatch). `tg store stats` reports archive bytes;
cleanup never deletes under the archive root. Config accepts optional
`[archive] root`. Docs: CONTRACT §13, guide/archive.md + index/README/SKILL/
MAP/FEATURES/PROPOSALS, store guide. Tests in `tests/test_cli_archive.py`.

**Decided:** Phase 1 CLI surface assumption from the task brief —
`tg archive backfill CHAT [CHAT …] [--limit N]` with explicit chats only —
locked into CONTRACT/guide so later phases can revise deliberately. Shared
architecture ceilings for cli/parser/preflight/dispatch grew within the
ADR-0058 grace band; integrator must ratchet them at merge (do not edit the
ceiling mirror on this branch).

**Learned:** argparse missing-subcommand / missing-CHAT exits as usage code 1
(not policy 2) through the existing `_parse`/`SystemExit` path — boundary
tests must match that taxonomy. Offline archive routing belongs in `cli.py`
beside `store`, not in `dispatch`, or the session bomb tests fail.

**Next:** Phase 2 proof-of-value gate (owner-selected dialogs + search
comparison) before full private backfill / delta sync.
