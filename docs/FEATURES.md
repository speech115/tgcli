# Feature Coverage Matrix

Answers one question checkably: "are all Telegram functions available?"
Source of truth: the TL schema of the pinned Telethon layer
(`telethon.tl.functions.*`) — the machine-readable form of
core.telegram.org/api. Maintained by `scripts/check-coverage.py` (phase 7):
the script fails if the installed layer has a namespace not listed here.

Status values:
- `wrapped` — dedicated `tg` command(s) exist
- `api` — reachable via `tg api` passthrough (ADR-0008), no wrapper needed yet
- `planned:<phase>` — wrapper scheduled
- `excluded` — deliberately not supported, reason given

Namespace list below is provisional until check-coverage first runs against
the pinned Telethon layer (phase 7 gate); do not trust it blindly before then.

| TL namespace | Status | Notes |
|--------------|--------|-------|
| messages | planned:1-2,4 | read/search/send wrappers; long tail via api |
| channels | planned:2,5 | info/subscribers wrappers; admin ops via api |
| account | api | profile/settings; lifecycle methods denylisted (ADR-0008) |
| auth | excluded | owned by `tg accounts` (login/import); raw auth denylisted |
| users | planned:2 | `tg info`; rest via api |
| contacts | api | resolve/search via api; wrapper only on demonstrated need |
| updates | excluded | pull-based CLI; no update loop (ADR-0002, no daemons) |
| upload | excluded | raw part-upload impractical over JSON; `tg media`/`tg send --file` own it |
| photos | api | |
| stories | api | |
| folders / chatlists | api | b1rd33/tg-cli wraps these; we wait for a real use case |
| stickers | api | |
| payments | api | read-only in practice; mutations gated like all writes |
| stats | api | |
| bots | api | user-account tool; bot management via api if ever needed |
| help | api | |
| langpack | api | |
| phone (calls) | excluded | voice/video needs a WebRTC media stack; out of scope |
| — secret chats | excluded | not part of the TL API Telethon implements |
| — Bot API (HTTP) | excluded | non-goal (PLAN.md); this is an MTProto user-account tool |
| — signup | excluded | account creation is a ToS/ban risk; import sessions instead |
