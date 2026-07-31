## 2026-07-31 — Thin `tg archive search` + FTS schema v2 (Cursor Grok)

**Did:** shipped offline `tg archive search QUERY [--chat CHAT] [--limit N]`
on `codex/archive-store`. Bumped archive `SCHEMA_VERSION` to 2 with
`messages_fts` tokenizer `unicode61 remove_diacritics 2`; v1 stores migrate
in place (drop/rebuild FTS from `messages` + transcript text). Exact FTS5
MATCH by default; `*` / other FTS operators pass through as raw MATCH (no
auto-prefix). Default limit 20 / hard cap 50; empty query and bad limits
exit 2; missing store or unknown `--chat` exit 4; readonly allowed; no
Telegram session. Wired `archive/search.py`, commands/parser/preflight/cli
offline path; CONTRACT §13, guide/archive, SKILL, MAP, FEATURES. Public-seam
tests cover empty query, caps, offline, chat filter, ё/е fold, prefix `*`,
missing store, v1→v2 migrate.

**Decided:** keep search thin for Phase 2 PoV — not full Phase 5 filters /
BM25 UX / paging. Cyrillic `ё`→`е` is folded at FTS write/query time because
current libsqlite `remove_diacritics 2` does not fold Cyrillic yo despite the
tokenizer setting (Latin diacritics still rely on the tokenizer).

**Learned:** FTS5 `WHERE alias MATCH ?` can misparse as a column compare;
use the bare table name `messages_fts MATCH ?`.

**Next:** owner re-run Phase 2 PoV via the CLI surface; independent Spec +
Standards review before merge.
