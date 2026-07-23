# Feature Proposals — Backlog

Owner wishlist reviewed 2026-07-21 against the real CLI surface (post-PR #17,
ADR-0028). This is a backlog of **not-yet-vetted** ideas, not a plan.

**Maintenance-mode gate (ADR-0026):** nothing here is approved. Any item needs
an explicit owner request + an ADR + a scoped plan before code.

## Already handled — do not re-propose

Much of the original wishlist already exists or is already tracked. Recorded
here so we don't rebuild it.

**Shipped (ADR-0028 / earlier):**

- `dialogs --unread-only` (+ `--kind user|group|channel`)
- `read --after-id` / `--before-id` / `--since` / `--until` / `--topic`
  — incremental & windowed reading (the interim change-feed path)
- `message --context N` — linear neighbour context
- global `search` (`--all`, `--from`, `--since`) — cross-dialog search
- `info --full` — role and rights preflight
- `tg doctor` — per-account `get_me` health: username, authorized, premium.
  Covers the "whoami / am I the right account?" need.
- `tg edit` / `tg delete` / `tg forward` / `tg mark-read` — preview→commit
- `send --reply-to` / `--file` / `--caption` / `--topic` / `--silent`,
  with markdown/entity parsing already applied on commit

**Already tracked as deferred issues (ISSUES.md) — don't duplicate here:**

- **MSG-001** — messaging tail: albums, scheduled send, `react`, `pin`,
  protect-content, explicit entities/formatting. Re-entry: first real task
  that names one.
- **FEED-001** — `tg changes` daemonless change feed. Shape agreed; needs its
  own ADR (updates-state, gap recovery, cursor format).
- **ACCOUNTS-001** — `tg accounts login` interactive (re)authorization.

If the owner wants any MSG-001 / FEED-001 item now, that is a re-entry on the
existing issue — not a new proposal.

---

## Genuinely new backlog

Everything below has no code path today and is not in ISSUES.md.

> **Graduated 2026-07-21 / shipped 2026-07-23:** `resolve`, `contacts
> list/search`, `media manifest`, `dialog pin/unpin` + `mark-unread`, and
> `thread` shipped under **ADR-0029** (all three plan slices). Still genuinely
> backlog: `mutual-chats`, bulk media download, incremental export, `batch`,
> `dialog archive/mute`, and the community/stats/security verticals.

**Value** = leverage; **Effort**: `XS` hours / `S` ~a day / `M` days+ADR /
`L` multi-day vertical + ADR; **Status**: `raw-only` = reachable via `tg api`
allowlisted calls but no task-first wrapper.

### Top quick wins (value ÷ effort)

1. **`tg resolve <@username | +phone>`** — `S`, high. Unified peer object
   `{id,type,username,display_name,is_contact,is_bot}`. Agent constantly needs
   a stable peer id ("Саша из вчера"). `resolveUsername`/`getContacts` are
   already read-allowlisted → ergonomic wrapper, not new capability.
2. **`tg contacts list` / `contacts search <q>`** — `S`, med. `getContacts` /
   `contacts.search`; raw-only today.
3. **`media manifest <chat> [--since]`** — `S`, med. Dry-run list (id, type,
   size, mime) before downloading; agent estimates volume then chooses. Cheap,
   high-leverage for backup/RAG.
4. **`tg dialog pin/unpin` + `mark-unread`** — `XS` each. `toggleDialogPin` /
   `markDialogUnread`. Small mutations behind the existing gate.
5. **`tg thread <chat> <id> [--replies] [--depth N]`** — `M`, high. Returns
   `{root, ancestors[], replies[]}` by walking reply chains (+ `getReplies`).
   `message --context` already covers linear neighbours; this covers the actual
   conversation. Read-only.

### Contacts & identity layer

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg resolve` | high | S | **shipped** (ADR-0029 slice 1) |
| `tg contacts list` | med | S | **shipped** (ADR-0029 slice 1) |
| `tg contacts search` | med | S | **shipped** (ADR-0029 slice 1) |
| `tg mutual-chats <@user>` | med | S | **shipped** (ADR-0032 slice 1) |

### Thread reading

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg thread` (reply chain) | high | M | **shipped** (ADR-0029 slice 3) |

### Read-only batch mode

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg batch --json` (JSONL in/out) | med-high | M | **shipped** (ADR-0032 slice 5; RO, cap 100, no doctor) |

One auth + one connection for N reads; less session-lock contention.
**First version read-only only** — batch mutations would be a home-grown
transaction language without real atomicity; out of scope for v1.

### Incremental export & bulk media

| Item | Value | Effort | Status |
|---|---|---|---|
| `export messages --after-id --append` / `--resume` | med | S | **shipped** (ADR-0032 slice 3) |
| `export bundle <chat> --output dir/` | med | M | missing |
| `media download --since/--type/--all/--message-ids` | med | M | **shipped** (ADR-0032 slice 4; no `--all`, hard cap 100) |
| `media manifest` | med | S | **shipped** (ADR-0029 slice 3) |

Note: incremental *reading* is already covered by `read --after-id/--since`;
this is about the *export/download* side.

### Dialog state management

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg dialog archive/unarchive` | med | S | **shipped** (ADR-0032 slice 2) |
| `tg dialog mute/unmute [--until]` | med | S | **shipped** (ADR-0032 slice 2; mute requires `--until` or `--forever`) |
| `tg dialog pin/unpin` | med | XS | **shipped** (ADR-0029 slice 2) |
| `tg dialog mark-unread` | med | XS | **shipped** as top-level `tg mark-unread` (ADR-0029 slice 2) |

Single `dialog` namespace (not five top-level commands). `mark-read` already
exists as a top-level command; keep that, add the rest here. Mutations → gate.

### Community / moderation vertical — defer

| Item | Value | Effort | Status |
|---|---|---|---|
| `members list/search` | med | M | raw-only reads |
| `join-requests list/approve/reject` | med | M | missing |
| `moderate mute/restrict/ban/unban/delete` | med | **L** | missing |
| `invite-links create/list/revoke` | med | M | missing |
| `chat join/leave` | med | S | missing |

Commercially strongest (moderator / community-CRM agents) but highest cost of
error. Any destructive op must show current account rights, exact target +
duration, count affected messages first, forbid mass-delete via a plain
`delete`, and carry a per-action cap. Build only against a concrete scenario,
one sub-namespace at a time, each with its own ADR. Do not bundle.

### Channel analytics — defer

| Item | Value | Effort | Status |
|---|---|---|---|
| `stats channel/message/growth/top-posts` | med | M | raw-only |

Four broadcast/megagroup/message stats reads already allowlisted (ADR-0010).
Wrapping is real work: `getBroadcastStats` returns opaque graph tokens needing
a second async graph-load + parsing. Secondary for a personal operator;
valuable as a channel-owner product.

### Account security surface — defer

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg security sessions` / `session show` | med | S | raw-only (`getAuthorizations`) |
| `tg security session terminate` | med | M | missing — dangerous |
| `tg security 2fa-status` / `login-alerts` | low | S | raw-only |

Listing is a straightforward read. `terminate` (`resetAuthorization`) can lock
the user out → hardened confirmation (exact device-id entry). Relevant given
the 2026-07 revocation incident behind ACCOUNTS-001.

---

## Suggested sequencing (new items only)

1. **Identity (S):** `resolve`, then `contacts`. Foundational, read-only.
2. **Small ergonomics (XS/S):** `dialog pin/mark-unread`, `media manifest`.
3. **Conversation quality (M):** `thread`.
4. **Data plumbing (S/M):** incremental export, bulk media, batch (RO).
5. **Verticals (M/L, per-scenario ADRs):** dialog state → community /
   moderation / stats / security — only against concrete demand.
