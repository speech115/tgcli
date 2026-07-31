## 2026-07-31 — Archive Phase 6 review, live acceptance, merge, release (Fable)

**Did:** reviewed the closing phase (one-shot `tg archive refresh`, failure
streak, launchd template) and ran the live acceptance that was assigned to
the integrator. The refresh run itself passed on the owner account — 132
events applied, 87 messages inserted, one edit recorded as a revision, media
and transcription stages clean, `failure_streak: 0`, exit 0 — and the
launchd template is a genuine one-shot (no `KeepAlive`, `RunAtLoad false`,
never installed by tgcli), so ADR-0002 holds.

**The live run is what earned its keep.** Its reconcile sweep reported a
mismatch, and inspecting the real store confirmed a pre-existing Phase 3
defect: a private dialog first seen through the delta feed was written with
`more=false`, so `backfill --private` treated it as fully walked and skipped
it forever. Four of ten tracked dialogs were affected; one held 9 messages
locally against 1306 on Telegram. Separately, Phase 6's own accounting fed
item-level media and transcription failures into the account failure streak
while media downloads had no attempt cap or terminal state — so one
permanently undownloadable voice note would grow the streak without bound
and latch `notification_sent`, permanently disarming the notification for
any later real outage, and `FLOOD_WAIT` backpressure counted as malfunction.

Both fixed in `5f8197f` and verified here rather than taken on report: the
media retry loop now terminates at `no_media` after three attempts, and a
live `backfill --private` recovered **1499 messages across 10 dialogs**,
including all four previously skipped, while correctly skipping the two
genuinely complete. Merged as `008bb31`.

**Integrator duties this entry rides with:** ceilings ratcheted (`cli.py`
613, `parser.py` 732, `preflight.py` 427, `dispatch.py` 327, `store.py`
1021, `sync.py` 596, `backfill.py` 314, `commands/archive.py` 545) and the
Phase 6 modules seeded (`archive/refresh.py` 146, `archive/media.py` 72,
`commands/archive_refresh.py` 111). Release `1.2.25` cut. This closes
wayfinder map #100: ADR-0068 phases 0–6 are all shipped.

**Learned — the defect family, now with four instances.** Phase 3 put a cap
between fetched data and the cursor that consumed it; Phase 4 bounded the
selection window instead of the work; Phase 6 defaulted a completion flag to
"complete" for history nobody had walked, and let per-item noise drive an
account-level outage signal. The rule that would have caught all four: a
flag or counter that means "done" must be written by the code that actually
finished, never by initialization or by a neighbouring failure — and any
"is it complete?" answer must come from the same query or run that did the
work.

**Next:** the archive is feature-complete; several dialogs still report
`more=true` and need repeated bounded `backfill --private` runs to finish
their history before the hourly launchd job is installed.
