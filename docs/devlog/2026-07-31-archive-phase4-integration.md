## 2026-07-31 — Archive Phase 4 review, merge, release (Fable)

**Did:** independent pre-merge review of Phase 4 (media fetch + local
transcription), delegated to the repo reviewer subagent and then verified
the blocking finding first-hand. Blocker: `_media_candidates` selected a
fixed 501-row window ordered by date over *all* transcribable rows instead
of the actually-undone ones, so once the newest 501 media items were on
disk, any older queued voice note became unreachable by every future run —
while `fetch_media`'s `remaining` flag, the operator's only drain signal,
reported `false`. Reproduced with a 700-item backlog (50 undone → 0
candidates, `remaining false`). Cursor fixed it in `bc80b8b`; re-ran the
same reproduction against the fix (50 → 50 candidates, `remaining true`,
and the file-vanished repair pass returns exactly the affected row).
Minors closed in the same commit: duplicate FTS `DELETE`, missing media
returning to the media queue without burning transcript attempts, CONTRACT
wording (`backfill` uses a fixed 50; `--max-media` is sync-only), plus
backlog/missing-media/cap regressions. Merged as `474aa06`.

**Integrator duties this entry rides with:** ceilings ratcheted for Phase 4
growth (`cli.py` 585, `parser.py` 663, `preflight.py` 374, `dispatch.py`
316) **and the archive package finally put under ceilings at all** —
`archive/store.py` 995, `sync.py` 588, `backfill.py` 310, `transcribe.py`
251, `search.py` 169, `commands/archive.py` 493. Phases 0–4 grew ~2.8k
lines there with no ratchet protection; seeding the ceilings at today's
size makes further growth deliberate. Release `1.2.23` cut.

**Learned:** the Phase-3 lesson generalized again, in a new disguise. There
the cap sat between fetched data and the cursor; here the bound was applied
to the *selection window* rather than to the work, so anything outside the
window was invisible forever. The reusable rule for the remaining phases:
bound the work, never the search space — and make any "is it drained?"
signal derive from the same query that does the draining.

**Next:** Phase 5 (full search surface: filters, BM25 + recency, paging,
`read`/`history` views) and Phase 6 (launchd packaging, failure surfacing,
release). `store.py` is the first split candidate if it grows again.
