## 2026-07-31 — Archive Phase 2 PoV remeasure (Cursor Grok)

**Did:** deepened the same five dialogs to `more: false`, then re-ran
the fixed probe set from
[2026-07-31-archive-phase2-pov-live.md](2026-07-31-archive-phase2-pov-live.md)
against live `tg search` and the thin Phase 5 CLI
`tg archive search` (HEAD `628dcc1`, FTS schema v2). Wrote gate
recommendation under the Fable criterion: archive must not lose any
**in-scope** probe (no live-only wins). Probe `--all` (#13) stayed
informational only.

### Setup

- Runtime: `.venv/bin/tg --json --account main` (needs unrestricted
  access to `~/.local/state/tgcli/`).
- Opening the store migrated FTS v1→v2 automatically (`schema_version: 2`).
- No new chats. No mutations (send/edit/delete/forward).
- Private `--chat` resolution: usernames are **not** in `scope` (only
  explicit channel/group adds are). Archive probes for privates used
  numeric `peer_id` (`307872069` / `459348548`). Channel/group used
  `groks` / `-1002719624138`. Documented as a thin-CLI UX gap, not a
  recall failure.

### Depth reached

Backfill loop: `archive backfill CHAT --limit 1000` until every dialog
reported `more: false`. **No FloodWait** (exit 5 never hit).

| Label | Kind | chatref | peer_id | Messages | more |
|-------|------|---------|---------|----------:|------|
| Private A | user | `brexit_man` | 307872069 | 12791 | false |
| Private B | user | `targetdaddy` | 459348548 | 5503 | false |
| Private C | user | `mannyachello` | 1072569605 | 9 | false (already full) |
| Channel X | channel | `groks` | -1001043217211 | 4274 | false |
| Group Y | group | `-1002719624138` | -1002719624138 | 4653 | false |
| **Total** | | | | **27230** | all false |

Prior PoV window was 1209 messages with 4/5 dialogs still `more: true`.

### Method

- Live: `tg search CHAT "query" --limit 10` (probe 13: `tg search --all`).
- Archive: `tg archive search "query" --chat CHAT --limit 10` (probe 13:
  global, no `--chat`). Prefix probe #12 used literal `хакатон*`.
- Score (presence in top-10): archive win / live win / tie / both miss.
- Diglog rows 14–20 did not always name an explicit chatref; chats were
  taken as the content home **inside the fixed five dialogs** (verified
  via archive LIKE + live sweep before scoring). Probe #20 uses `gsd` on
  Private A (same frequent-token band; diglog listed three tokens across
  slots 17–20).

### Gate criterion (Fable)

**Pass iff** no in-scope probe is a live-only win. Morphology probe #11
(`хакатон` singular) is in-scope and counts. Probe #13 (`--all OpenAI`)
is excluded from the gate score.

### In-scope score table (tasks 1–12, 14–20)

| # | Chat | Query | Live n | Archive n | Score |
|---|------|-------|-------:|----------:|-------|
| 1 | Private A | `дипсик` | 10 | 10 | tie |
| 2 | Private A | `мэтта кока` | 1 | 1 | tie |
| 3 | Private A | `недельной квоты` | 2 | 1 | tie |
| 4 | Private A | `Luna теперь дешевле` | 1 | 1 | tie |
| 5 | Private B | `закибербулили` | 2 | 2 | tie |
| 6 | Private B | `Кахетии` | 2 | 1 | tie |
| 7 | Channel X | `SK Hynix` | 5 | 5 | tie |
| 8 | Channel X | `эпидемию тупости` | 1 | 1 | tie |
| 9 | Group Y | `elevenlabs` | 10 | 9 | tie |
| 10 | Group Y | `хакатоны S26` | 1 | 1 | tie |
| 11 | Group Y | `хакатон` | 10 | 7 | tie |
| 12 | Group Y | `хакатон*` | 10 | 10 | tie |
| 14 | Private A | `стрей` | 1 | 1 | tie |
| 15 | Channel X | `Андрея Свинцова` | 1 | 1 | tie |
| 16 | Group Y | `github логинами` | 1 | 1 | tie |
| 17 | Private B | `сралина` | 10 | 10 | tie |
| 18 | Channel X | `бульдозером` | 3 | 1 | tie |
| 19 | Group Y | `gsd` | 2 | 1 | tie |
| 20 | Private A | `gsd` | 10 | 10 | tie |

**In-scope totals:** archive win 0 / live win **0** / tie **19** / both miss 0.

### Informational only

| # | Query | Live | Archive | Note |
|---|-------|------|---------|------|
| 13 | `--all OpenAI` vs global archive | 10 | 10 | Live hits span peers outside archive scope; archive is archived-peers-only by design. Excluded from gate. |

### Latency samples (same machine)

| Query | Live `tg search` | `tg archive search` (CLI wall) | Raw FTS MATCH |
|-------|-----------------:|-------------------------------:|--------------:|
| `дипсик` | ~1680 ms | ~621 ms | ~0.65 ms |
| `SK Hynix` | ~2168 ms | ~813 ms | ~0.11 ms |
| `elevenlabs` | ~1471 ms | ~550 ms | ~0.06 ms |

CLI archive wall time is process startup + open/query, not MATCH cost.
Raw local MATCH remains sub-millisecond (~1000–30000× vs live RPC).

### What changed vs the first PoV

- Depth gap closed: all five dialogs `more: false` (was 300-cap / more).
- Morphology #11 flipped from **live win** → **tie** (deeper index + FTS
  v2 unicode61; archive still does not stem `хакатоны`→`хакатон` for
  every live id, but finds in-scope hits).
- Frequent / edge probes that previously lost on *depth* now tie on
  presence inside the full local window.
- Archive side now uses shipped `tg archive search` instead of ad-hoc SQL.

### Verdict / gate recommendation

**GATE: PASS** — zero live-only wins on in-scope probes (19/19 tie),
including morphology #11.

Caveats that remain true and should not be papered over:

- Rank/depth parity is not identity: several ties still show live
  returning more ids in the top-10 (`бульдозером` 3 vs 1, `elevenlabs`
  10 vs 9, `хакатон` 10 vs 7).
- Global `--all` is structurally unfair until multi-dialog scope expands
  (Phase 3+); still informational live-skewed by peer coverage.
- Private `--chat` username resolution is missing for standing private
  peers (peer_id required).
- No edit/delete/tombstone or transcript proof in this run
  (`revisions/tombstones/transcripts: 0`).

**Next:** owner confirm Phase 2 gate; if shipping search further, fix
private `--chat` resolution and decide whether top-k depth/rank parity
(not just presence) becomes a Phase 3 bar.
