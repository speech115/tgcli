# Feature Coverage Matrix

Answers one question checkably: "are all Telegram functions available?"
Source of truth: the TL schema of the pinned Telethon layer
(`telethon.tl.functions.*`) — the machine-readable form of
core.telegram.org/api. Maintained by `scripts/check-coverage.py` (phase 7):
the script fails if the installed layer has a namespace not listed here.

Status values:
- `wrapped` — dedicated `tg` command(s) exist
- `run` — no dedicated command; reachable from a `tg run` script (reads freely, writes with `--write`)
- `planned:<phase>` — wrapper scheduled
- `denied` — `tg run` refuses the namespace, reads and writes alike
- `excluded` — deliberately not supported, reason given

| TL namespace | Status | Notes |
|--------------|--------|-------|
| account | denied | Session lifecycle is owned by `tg accounts`; `tg run` refuses account calls, reads and writes alike (ADR-0010/ADR-0092). |
| aicompose | run | No dedicated workflow; reachable from `tg run`. |
| auth | denied | Session lifecycle is owned by `tg accounts`; `tg run` refuses auth calls except Telethon's cross-DC download export. |
| bots | run | User-account tool; bot-management calls go through `tg run`. |
| channels | wrapped | `info`, `count`, media, subscriber export, and `clone init` cover daily work; `tg run` covers the long tail. |
| chatlists | run | No dedicated workflow; reachable from `tg run`. |
| contacts | wrapped | `contacts list` / `search` and `resolve` cover daily identity work; `tg run` covers the long tail (ADR-0029). |
| folders | run | No dedicated workflow; folder and chatlist calls go through `tg run`. |
| fragment | run | No dedicated workflow; reachable from `tg run`. |
| help | run | No dedicated workflow; reachable from `tg run`. |
| langpack | run | No dedicated workflow; reachable from `tg run`. |
| messages | wrapped | `read`, `search`, `latest`, `message`, `send`, `edit`, `delete`, `forward`, drafts, export, `clone sync` (native forward + protected reupload), `archive` backfill/sync/transcribe/search/read/history (ADR-0068), and `transcribe` voice notes (ADR-0075) cover daily work; `tg run` covers the long tail. |
| payments | run | No dedicated workflow; reachable from `tg run`, writes with `--write`. |
| phone | excluded | Voice and video calls need a WebRTC media stack and are out of scope. |
| photos | run | No dedicated workflow; `getUserPhotos` is a plain read from `tg run`. |
| premium | run | No dedicated workflow; reachable from `tg run`. |
| smsjobs | run | No dedicated workflow; reachable from `tg run`. |
| stats | run | No dedicated workflow; broadcast, megagroup, and message stats are plain reads from `tg run`. |
| stickers | run | No dedicated workflow; reachable from `tg run`. |
| stories | run | `media download` covers story links with optional `--codec` encoding selection (ADR-0076); story reads go through `tg run`. Story publish/delete stay out. |
| updates | wrapped | `archive sync` keeps the archive current through the GetDifference cursor engine (ADR-0063, ADR-0068). |
| upload | run | Wrapped media and send paths own file transfer, including protected clone reupload; `getFile` is a plain read from `tg run`. |
| users | wrapped | `info` covers daily identity inspection; `tg run` covers the long tail. |

## Non-TL exclusions

- **Secret chats** — not part of the TL API Telethon implements.
- **Bot API (HTTP)** — non-goal; tgcli is an MTProto user-account tool.
- **Signup** — account creation is a ToS and ban risk; authorize with
  `tg accounts login --phone PHONE`, or `tg accounts import` for an old-stack
  session.
- **Local archive search/sync/transcribe** — ADR-0068 ships private
  `--private` backfill, `archive sync` / `rebaseline`, bounded media
  acquisition, local Parakeet transcription, gap reporting, filtered/ranked
  offline search, and read/history exploration. Archive purge/rebuild and
  off-machine backup remain deferred.
