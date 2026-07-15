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
| account | api | Raw account calls use the audited write gates; lifecycle denylist remains permanent (ADR-0010). |
| aicompose | api | No dedicated workflow; use raw TL only after task-specific review. |
| auth | excluded | Session lifecycle is owned by `tg accounts`; raw auth calls are denylisted. |
| bots | api | User-account tool; bot-management calls are raw TL only. |
| channels | wrapped | `info`, `count`, media, subscriber export, and `clone init` cover daily work; raw TL covers the long tail. |
| chatlists | api | No demonstrated daily workflow needs a wrapper. |
| contacts | api | `resolveUsername`, `search`, and `getContacts` are allowlisted reads; other calls use raw safety gates. |
| folders | api | No demonstrated daily workflow needs a wrapper. |
| fragment | api | No dedicated workflow; use raw TL only after task-specific review. |
| help | api | No dedicated workflow; use raw TL only after task-specific review. |
| langpack | api | No dedicated workflow; use raw TL only after task-specific review. |
| messages | wrapped | `read`, `search`, `latest`, `message`, `send`, export, and `clone sync` (native forward + protected reupload) cover daily work; raw TL covers the long tail. |
| payments | api | No dedicated workflow; mutations remain behind raw write safety gates. |
| phone | excluded | Voice and video calls need a WebRTC media stack and are out of scope. |
| photos | api | `getUserPhotos` is an allowlisted read; other calls use raw safety gates. |
| premium | api | No dedicated workflow; use raw TL only after task-specific review. |
| smsjobs | api | No dedicated workflow; use raw TL only after task-specific review. |
| stats | api | Four broadcast, megagroup, and message stats reads are allowlisted (ADR-0010). |
| stickers | api | No dedicated workflow; use raw TL only after task-specific review. |
| stories | api | No dedicated workflow; use raw TL only after task-specific review. |
| updates | excluded | Current CLI has no update loop; clone catch-up is an explicit foreground invocation, not a watcher. |
| upload | excluded | Raw part-upload remains impractical over JSON; wrapped media/send paths own it, including protected clone reupload. |
| users | wrapped | `info` covers daily identity inspection; raw TL covers the long tail. |

## Non-TL exclusions

- **Secret chats** — not part of the TL API Telethon implements.
- **Bot API (HTTP)** — non-goal; tgcli is an MTProto user-account tool.
- **Signup** — account creation is a ToS and ban risk; import authorized
  sessions instead.
