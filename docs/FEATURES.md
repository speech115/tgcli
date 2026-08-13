# Feature Coverage Matrix

Answers one question checkably: "are all Telegram functions available?"
Source of truth: the TL schema of the pinned Telethon layer
(`telethon.tl.functions.*`) — the machine-readable form of
core.telegram.org/api. Maintained by `scripts/check-coverage.py` (phase 7):
the script fails if the installed layer has a namespace not listed here.

Status values:
- `wrapped` — dedicated `tg` command(s) exist
- `api` — reachable via `tg api` passthrough only when explicitly allowlisted (ADR-0010), no wrapper needed yet
- `planned:<phase>` — wrapper scheduled
- `excluded` — deliberately not supported, reason given

| TL namespace | Status | Notes |
|--------------|--------|-------|
| account | excluded | Session lifecycle is owned by `tg accounts`; raw account calls are denylisted wholesale, reads and writes alike (ADR-0010/ADR-0092). |
| aicompose | api | No dedicated workflow; use raw TL only after task-specific review. |
| auth | excluded | Session lifecycle is owned by `tg accounts`; raw auth calls are denylisted. |
| bots | api | User-account tool; bot-management calls are raw TL only. |
| channels | wrapped | `info`, `count`, media, subscriber export, and `clone init` cover daily work; raw TL covers the long tail. |
| chatlists | api | No demonstrated daily workflow needs a wrapper. |
| contacts | wrapped | `contacts list` / `search`, `resolve`, and `mutual-chats` cover daily identity work; raw TL covers the long tail (ADR-0010 / ADR-0029). |
| folders | wrapped | `dialog archive` / `unarchive` covers peer folder moves; other folder/chatlist calls stay on raw TL. |
| fragment | api | No dedicated workflow; use raw TL only after task-specific review. |
| help | api | No dedicated workflow; use raw TL only after task-specific review. |
| langpack | api | No dedicated workflow; use raw TL only after task-specific review. |
| messages | wrapped | `read`, `search`, `latest`, `message`, `send`, export, `clone sync` (native forward + protected reupload), `archive` backfill/sync/transcribe/search/read/history, typed recurring `jobs` (ADR-0068/0087), `transcribe` voice notes (ADR-0075), and `changes` cover daily work; raw TL covers the long tail. |
| payments | api | No dedicated workflow; mutations remain behind raw write safety gates. |
| phone | excluded | Voice and video calls need a WebRTC media stack and are out of scope. |
| photos | api | `getUserPhotos` is an allowlisted read; other calls use raw safety gates. |
| premium | api | No dedicated workflow; use raw TL only after task-specific review. |
| smsjobs | api | No dedicated workflow; use raw TL only after task-specific review. |
| stats | api | Four broadcast, megagroup, and message stats reads are allowlisted (ADR-0010). |
| stickers | api | No dedicated workflow; use raw TL only after task-specific review. |
| stories | api | `getPeerStories`, `getStoriesArchive`, `getStoriesByID`, `getStoryViewsList` reads are allowlisted (ADR-0010); `media download` covers story links with optional `--codec` encoding selection (ADR-0076). Story publish/delete stay out. |
| updates | wrapped | `tg changes` (ADR-0063) plus `archive sync` reuse of the same cursor/GetDifference seam (ADR-0068 Phase 4). |
| upload | api | `getFile` is an allowlisted read (ADR-0010); raw part-upload remains impractical over JSON, wrapped media/send paths own it, including protected clone reupload. |
| users | wrapped | `info` covers daily identity inspection; raw TL covers the long tail. |

## Non-TL exclusions

- **Secret chats** — not part of the TL API Telethon implements.
- **Bot API (HTTP)** — non-goal; tgcli is an MTProto user-account tool.
- **Signup** — account creation is a ToS and ban risk; authorize with
  `tg accounts login --phone PHONE`, or `tg accounts import` for an old-stack
  session.
- **Local archive search/sync/transcribe** — ADR-0068/0087 ship private
  `--private` backfill, `archive sync` / `rebaseline`, bounded media
  acquisition, local Parakeet transcription, gap reporting, filtered/ranked
  offline search, read/history exploration, and independent Telegram/local
  recurring jobs with manual launchd templates. Archive purge/rebuild and
  off-machine backup remain deferred.
