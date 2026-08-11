## 2026-08-11 — Foreground jobs cutover (Codex)
**Did:** completed ADR-0087 slice #189 on `codex/issue-189-jobs-cutover`.
Added safe recurring `jobs run --rearm KEY`: completed work creates an
identical next generation, queued work runs as-is, and failed, cancelled, or
running work is refused. Added one privacy-bounded best-effort macOS
notification when a generation first becomes failed. Replaced the old archive
composition plist with independent Telegram and local jobs templates. Removed
`tg archive refresh`, its composition/command modules, failure-streak state,
guide, parser/preflight/dispatch paths, and compatibility surface. Updated the
contract, README, skill routing, guides, MAP, issues/proposals, and architecture
ratchet. Full gate: 1881 passed, 9 skipped; coverage, docs, pyright, ruff, and
architecture checks passed.
**Decided:** the checked-in recurring keys are `archive-sync` and
`archive-transcribe`. A timer cannot resurrect an operator cancellation or a
failed outage. Failure notifications contain only the key and the command to
inspect it. The archive database ignores old refresh columns and creates no new
ones; there is no command alias or migration ceremony for removed behavior.
**Learned:** live local acceptance completed one real transcription quantum
under `TGCLI_NO_SEND=1` (`attempted=1`, `transcribed=1`, no errors). Telegram
acceptance could not begin because no explicit `job` role was authorized; the
runner correctly refused to fall back to primary. Offline clone cursors alone
do not prove a caught-up no-op, so no clone mutation was attempted.
**Next:** independent whole-diff review, then merge #189 into the campaign
branch. Complete Telegram live acceptance only after explicit role
authorization is available.
