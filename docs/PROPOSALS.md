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
> backlog: `export bundle`, and the community/stats/security verticals.
> ADR-0032 shipped: `mutual-chats`, `dialog archive/mute`, incremental
> export, bulk media download, and read-only `tg batch`.

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

**`failed` should distinguish temporary from permanent (XS).** wacli only
marks media unavailable once both the phone and the CDN path are confirmed
gone, so later runs skip genuinely dead rows. We have no local DB to hold such
a flag and never will, but the *problem* transfers: today bulk
`media download` records `{"message_id", "error"}` in `failed`
(`src/tgcli/commands/media.py`) with no kind, so a re-run re-fetches
permanently dead messages (`FILE_REFERENCE_EXPIRED` on a deleted source) and
fails them again — wasted traffic and FLOOD_WAIT on a channel backup. Add a
`kind: "temporary" | "permanent"` to each `failed` entry so a stateless caller
can decide whether to retry. Fits the existing shape; no state.

### Accounts surface symmetry (from the wacli review)

wacli's account model is `list / add / use / show / remove`; the
active-account resolution order (`--store` → `--account` → env →
`default_account` → legacy) already matches ours in `config.py`, so nothing to
copy there. But our surface is asymmetric: `tg accounts import` + `list` exist,
`show` and `remove` do not. Both are the missing half of the
new-machine / broken-session story behind ACCOUNTS-001.

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg accounts show <alias>` | med | S | missing |
| `tg accounts remove <alias>` | low-med | XS | missing |

- **`show`** — session path, lock holder, authorized state, *without opening a
  connection*. This is literally the offline branch of `doctor --connect`
  (see the Agent-surface subsection); design them as one command, not two.
- **`remove`** — drop an account from config; today that means hand-editing the
  TOML plus deleting the `.session`. A config mutation, not a Telegram one —
  fits the existing gate trivially.

Not worth importing: `add` (our `import` already creates an account from the
old stack) and `use` (a persistent "current account" is cross-invocation state
that just duplicates the working `--account`). Both `show` and `remove` land in
the same PR as ACCOUNTS-001.

### Checked and rejected (wacli media / read-only)

- `--read-only` media download with explicit `--output`: wacli allows it
  because writing a file you named is not a store mutation. We are already
  stricter-correct — `media download` never sits behind the `--readonly` gate
  (`preflight.py`) because downloading to disk changes nothing in Telegram.
  Nothing to add.

### Full-coverage sweep of the remaining wacli pages (2026-07-23)

All 26 wacli doc pages were read (12 in the main review + the rest swept by two
cheap sub-agents, findings re-checked against the tgcli code). The remaining 14
pages produced almost nothing new — most are already-have or meta:

- **ALREADY-HAVE:** `channels`, `chats` (our `dialog`/`mark-*`), `version`,
  `help` (argparse gives both), and — the sub-agents missed this — release
  discipline (`CHANGELOG.md` + ADR-0038 already do one-feature-one-release).
  Profile *reads* too: `users.getFullUser` and `photos.getUserPhotos` are
  already read-allowlisted, so reading a profile works via `tg api` today.
- **SKIP:** `groups` (creation is a moderation-vertical gap, already parked
  there), `contacts-import-system` (platform binding + external name source,
  belongs in `tg-agent`), `install`/`overview`/`quickstart`/`docs` (meta), and
  Homebrew packaging (uv is the deliberate choice — see the Go-vs-Python note).

Three genuine but low-value gaps, recorded for completeness, none urgent:

| Item | Value | Effort | Note |
|---|---|---|---|
| `tg completion bash\|zsh\|fish` | low | S | Human-only QoL; agents never need it. We are argparse, so this needs `argcomplete` or a hand-rolled generator — **not** Click as a naive port assumes. |
| `tg profile set --bio/--name/--photo` | low | S | Self-account mutation → existing gate. Profile *read* is already raw-only; only the write half is missing. Niche for a correspondence tool. |
| `tg presence typing/recording` | low | XS | Genuine gap but marginal under statelessness: a one-shot invocation sets the indicator and disconnects, so it vanishes almost immediately. Only meaningful if bundled into the same connection as a send. |

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

### Agent surface — from the wacli review (2026-07-23)

Reviewed [wacli](https://wacli.sh/), openclaw's WhatsApp CLI and a sibling of
the gogcli lineage tgcli inherited, for transferable decisions. Most of it does
not transfer: its local SQLite + FTS5 mirror, `sync --follow`, and in-tool
webhook fan-out all compensate for a protocol with no server-side search and no
readable history. Telegram has both, so a mirror inside tgcli would buy a second
source of truth and nothing else. Four items survive the filter. (A fifth
finding — session-lock contention — is a blocker on FEED-001, recorded in
ISSUES.md, not here.)

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg store stats` / `store cleanup` | med-high | S | missing |
| `--events` NDJSON lifecycle stream | med-high | M | missing |
| `tg doctor` offline by default + `--connect` | med | S | missing |
| `tg spec --json` | med | S | **re-proposal against ADR-0028** |

**`tg store`.** Nothing ever cleans `~/.local/state/tgcli`. Measured
2026-07-23 on the owner's machine: 59 preview files of which **51 are `.used`**
— burnt previews retained forever, one of them 17 KB of message text, mode
`0644`; `audit.jsonl` at 1.1 MB and unbounded; and 340 KB under `mirrors/`,
`mirror-lab/`, `labs/`, `probes/` left by the removed `tg mirror` surface and
old experiments. The state root itself is `0700`, so nothing leaks off-account,
but stale message bodies with no expiry are still the wrong default. wacli's
shape fits directly: `store stats` plus `store cleanup [--older-than N]
[--dry-run] [--confirm]`, with the same explicit disclaimer that it only
touches local artefacts and never Telegram. Also fixes the permission
inconsistency: `audit.jsonl` is `0600` while `invocations.jsonl` and previews
are `0644`.

**`--events`.** `tg clone sync`, `tg export messages`, and bulk `media
download` run silently and emit only on completion — an agent cannot
distinguish a live run from a hung one. wacli emits one NDJSON lifecycle event
per stderr line; that stream choice fits CONTRACT §2 unchanged (stderr already
owns progress) and does not compete with the single stdout document.
`clone.py` already counts `copied_batches`. Needs an ADR: which commands opt
in, the event vocabulary, and whether events are contract-stable.

**`tg doctor --connect`.** Today `doctor` always opens a session, so it cannot
run when the session is revoked or the network is down — precisely when it is
needed. wacli splits it: offline by default (store layout, auth state, locks),
`--connect` adds live checks. Our offline branch would cover config validity,
file permissions, session presence, lock holder, and state size. Natural
companion to ACCOUNTS-001, which needs a diagnosis path that works on a broken
session.

**`tg spec`.** *Not a wacli import* — wacli's `spec` is a documentation page,
not a command; the review only prompted the re-examination. Listed here for
provenance. Rejected by ADR-0028 as "a second source of truth that drifts".
That objection was right at the time and is weaker now: ADR-0034 made
`parser.py` grammar-only, so a generated spec is a projection of the single
source rather than a copy of it. The value is that an agent asks the binary
what it can do instead of trusting `SKILL.md` to have kept up. Any re-entry
must overturn the ADR-0028 line explicitly. Shell completion is the same
generator with a different renderer — bundle it or drop it, not a separate
item.

**Checked and rejected.** wacli distinguishes "server accepted" from
"delivered to recipient" because WhatsApp is E2E and a device may fail to
decrypt the first copy. In Telegram a returned `message_id` means the message
is on the server in the chat, so `tg send --commit`'s
`{"preview_id", "message_id"}` already carries the honest meaning; no new field
needed. `--pick N` for ambiguous recipients is likewise moot — Telegram
usernames are unique, and display-name search already returns a list.

---

## Suggested sequencing (new items only)

1. **Identity (S):** `resolve`, then `contacts`. Foundational, read-only.
2. **Small ergonomics (XS/S):** `dialog pin/mark-unread`, `media manifest`.
3. **Conversation quality (M):** `thread`.
4. **Data plumbing (S/M):** incremental export, bulk media, batch (RO).
5. **Verticals (M/L, per-scenario ADRs):** dialog state → community /
   moderation / stats / security — only against concrete demand.
