## 2026-07-31 — Archive Phase 2 live PoV (Cursor Grok)

**Did:** ran an owner-account (`main`) proof-of-value on branch
`codex/archive-store`: init local archive, backfill five dialogs, invent
search tasks from real message text, compare live `tg search` vs offline
SQLite FTS5 (`messages_fts` MATCH). Phase 5 `tg archive search` is **not
shipped** — archive side used direct FTS queries against
`~/.local/state/tgcli/archive/main/archive.db`.

### Setup

- Runtime: `.venv/bin/tg --json --account main` (Telethon 1.44.0).
- `tg doctor`: session lock free, state writable (needed unrestricted
  permissions for `~/.local/state/tgcli/` writes).
- `archive init` → created store; `archive add` for channel + group;
  private 1:1 dialogs needed no add (standing private scope).
- Backfill: `--limit 300` on five chats → **1209** messages stored.

### Dialogs (lightly anonymized)

| Label | Kind | chatref | Stored | Window note |
|-------|------|---------|--------|-------------|
| Private A | user | `brexit_man` | 300 | `more: true` (older history remains on Telegram) |
| Private B | user | `targetdaddy` | 300 | `more: true` |
| Private C | user | `mannyachello` | 9 | full history; too thin for search PoV |
| Channel X | channel | `groks` | 300 | `more: true` |
| Group Y | group | `-1002719624138` | 300 | title: AI mindset {space}; `more: true` |

### Method honesty

- Live: `tg search CHAT "query" --limit 10` (and one `--all`).
- Archive: `messages_fts MATCH …` joined to `messages` for peer_id /
  message_id / snippet. Phrase queries use FTS5 quoted phrases; one
  adversarial probe used prefix `хакатон*`.
- No sends/edits/deletes/forwards. No phone numbers or session material
  in this write-up.

### Primary search tasks (~10)

Queries invented from messages that **should** hit known backfilled text.

| # | Chat | Query | Live | Archive FTS | Score | Why |
|---|------|-------|------|-------------|-------|-----|
| 1 | Private A | `дипсик` | 10 | 8 | tie | both recall; live also returns older hits outside window |
| 2 | Private A | `мэтта кока` | 1 | 1 | tie | exact phrase; overlapping id |
| 3 | Private A | `недельной квоты` | 2 | 1 | tie | both hit Terra/quota note |
| 4 | Private A | `Luna теперь дешевле` | 1 | 1 | tie | OpenAI price-drop post |
| 5 | Private B | `закибербулили` | 2 | 1 | tie | rare slang token |
| 6 | Private B | `Кахетии` | 2 | 1 | tie | unique place name |
| 7 | Channel X | `SK Hynix` | 5 | 2 | tie | Aschenbrenner/HBM post |
| 8 | Channel X | `эпидемию тупости` | 1 | 1 | tie | Economist piece |
| 9 | Group Y | `elevenlabs` | 10 | 3 | tie | TTS thread; live deeper |
| 10 | Group Y | `хакатоны S26` | 1 | 1 | tie | hackathon offer exact form |

**Primary score:** 10/10 tie on in-window recall of known phrases.

### Adversarial / differentiating probes

| # | Query | Score | Lesson |
|---|-------|-------|--------|
| 11 | Group Y `хакатон` (singular) | **live win** | Telegram stemming finds `хакатоны`; raw FTS5 exact token miss. Also returns ids far below backfill floor. |
| 12 | Group Y `хакатон*` | mixed | FTS prefix recovers 1 in-window hit; live still deeper. |
| 13 | `--all OpenAI` vs global FTS | **live win** | Live spans dialogs not in archive scope; archive only sees backfilled peers. |
| 14–16 | Edge-of-300-window phrases (`стрей`, `Андрея Свинцова`, `github логинами`) | tie | Content inside the backfill window is findable both ways. |
| 17–20 | Frequent tokens (`сралина`, `бульдозером`, `gsd`) | **live win** on *depth* | Both find recent; live returns many older ids outside the 300-msg cap. |

### Latency (same machine, three representative queries)

| Query | Live `tg search` | Archive FTS |
|-------|------------------|-------------|
| `дипсик` | ~1018 ms | ~0.6 ms |
| `SK Hynix` | ~1777 ms | ~0.5 ms |
| `elevenlabs` | ~1101 ms | ~0.3 ms |

Archive FTS is ~**1000–3000×** faster for local MATCH. Offline capability is
architectural (no network) — not disconnected in this run, but the path
does not call Telegram.

### What this proves / does not prove

**Proves**

- Phase 1/2 store + FTS5 index is real and queryable after `backfill`.
- For phrases that exist inside the backfilled window, archive recall
  matches live for practical agent lookup.
- Local latency is agent-grade (sub-millisecond MATCH).

**Does not prove**

- Archive as a replacement for Telegram global/morphological search.
- Completeness beyond `--limit 300` (hard cap 1000/dialog; `more: true`
  on 4/5 dialogs).
- Phase 5 CLI UX (`tg archive search`) — not shipped; this PoV used
  offline SQL.
- Edit/delete/tombstone fidelity (no revisions/tombstones in this run).
- Voice/transcript search (`transcripts: 0`).

### Verdict recommendation (owner still gates)

**MIXED** — archive is meaningfully better for **offline + speed +
deterministic in-window text lookup** on backfilled dialogs; live remains
better for **morphology, depth beyond the backfill window, and
cross-dialog `--all` coverage**. Ship Phase 5 search + deeper sync before
calling archive a search replacement.

**Next:** owner decide gate; if yes, prioritize Phase 5 CLI search
(stemming/prefix policy) and continuous sync so the local window stops
losing to Telegram depth.
