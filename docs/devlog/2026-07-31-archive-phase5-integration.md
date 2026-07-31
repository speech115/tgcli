## 2026-07-31 — Archive Phase 5 review, merge, release (Fable)

**Did:** independent pre-merge review of Phase 5 (search filters, BM25 +
paging, offline `read`/`history`, `tg://` handoff). Reviewed first against
the defect family that hit phases 3 and 4 — a bound placed on the wrong
thing — and **it did not recur**: every filter is applied in the same SQL
query that limits (nothing post-filtered in Python after LIMIT), ordering
is total via a `peer_id, message_id` tiebreak, and `has_more` comes from
that same query through the `limit + 1` trick. Verified on a fixture where
25 rows shared one rank and one date: 25 unique hits across three pages,
no duplicates, no skips.

One Important finding: `--from` compared a Python `casefold()` against
SQLite's ASCII-only `lower()`, so `--from Мария` silently returned zero
hits in a Russian-language archive while the message existed — reproduced
directly. Minors: malformed raw FTS5 queries surfaced as a RUNTIME exit 1
rather than a usage error; the superseded `archive/search.py::search()`
was left in place with no callers, a second search implementation free to
drift; `tg_link` emitted `openmessage?chat_id=` for every peer type, which
does not match the documented forms for users or channels. All fixed on
the branch in `bb4f50b` and re-verified here (Мария/мария/МАРИЯ all match;
exit 2; dead function gone; links now `openmessage?user_id`,
`privatepost?channel=<unmarked>`, `resolve?domain=…&post=`). Merged as
`5521012`.

**Integrator duties this entry rides with:** ceilings ratcheted (`cli.py`
609, `parser.py` 702, `preflight.py` 417, `commands/archive.py` 533,
`store.py` 1001) and the two Phase 5 modules seeded — `archive/explore.py`
578 (new, ADR-0069) and `archive/search.py` down to 78 now that its query
path is superseded. Release `1.2.24` cut.

**Learned:** the cross-language case-folding mismatch is worth a standing
check in this repo — Python `casefold()`/`lower()` and SQLite `lower()`
disagree on every non-ASCII alphabet, and this archive is Russian-first,
so any future SQL-side text comparison must fold through a registered
Python function rather than SQLite's builtin.

**Next:** Phase 6 — `tg archive refresh` composition, the launchd plist
template and guide, consecutive-failure notification, then the closing
release. `store.py` remains the first split candidate.
