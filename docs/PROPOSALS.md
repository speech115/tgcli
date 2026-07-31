# Feature Proposals — Backlog

Owner wishlist first reviewed 2026-07-21 against the real CLI surface
(post-PR #17, ADR-0028) and status-maintained as items graduate or ship.
This is a backlog of **not-yet-vetted** ideas, not a plan.

**Maintenance-mode gate (ADR-0026):** no current backlog row here is
approved. Shipped rows are retained only as provenance; every remaining item
needs an explicit owner request + an ADR + a scoped plan before code.

## Already handled — do not re-propose

Much of the original wishlist already exists or is already tracked. Recorded
here so we don't rebuild it.

**Shipped — do not re-propose:**

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
- `accounts login|show|remove` (ADR-0042), plus named session roles
  (ADR-0062)
- `tg changes` daemonless change feed (ADR-0063, released in `1.2.19`)
- SQLite/WAL clone state with JSON import/export rollback (ADR-0060,
  released in `1.2.19`)

**Already tracked as deferred issues (ISSUES.md) — don't duplicate here:**

- **MSG-001** — messaging tail: albums, scheduled send, `react`, `pin`,
  protect-content, explicit entities/formatting. Re-entry: first real task
  that names one.

If the owner wants a remaining MSG-001 item now, that is a re-entry on the
existing issue — not a new proposal.

---

## Genuinely new backlog

Tables below retain shipped rows as provenance. Only rows whose status is
`missing`, `raw-only`, or explicitly deferred are current backlog.

> **Graduated 2026-07-21 / shipped 2026-07-23:** `resolve`, `contacts
> list/search`, `media manifest`, `dialog pin/unpin` + `mark-unread`, and
> `thread` shipped under **ADR-0029** (all three plan slices). Still genuinely
> backlog: `export bundle`, and the community/stats/security verticals.
> ADR-0032 shipped: `mutual-chats`, `dialog archive/mute`, incremental
> export, bulk media download, and read-only `tg batch`.

**Value** = leverage; **Effort**: `XS` hours / `S` ~a day / `M` days+ADR /
`L` multi-day vertical + ADR; **Status**: `raw-only` = reachable via `tg api`
allowlisted calls but no task-first wrapper.

### Historical quick-win ranking (all shipped)

1. **`tg resolve <@username | +phone>`** — `S`, high. Unified peer object
   `{id,type,username,display_name,is_contact,is_bot}`. Agent constantly needs
   a stable peer id ("Саша из вчера"). `resolveUsername`/`getContacts` are
   were already read-allowlisted → ergonomic wrapper, not new capability.
2. **`tg contacts list` / `contacts search <q>`** — `S`, med. `getContacts` /
   `contacts.search`; this was raw-only before ADR-0029.
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

wacli's account model is `list / add / use / show / remove`. Its selection
chain is broader than ours: `--store` → `--account` → env → default → legacy,
while `tgcli.config.resolve_account()` deliberately has only explicit
`--account` → `TGCLI_ACCOUNT` → `default_account`. A direct store override and
legacy fallback do not transfer to tgcli's configured-session model. Our
command surface was asymmetric: `tg accounts import` + `list` existed while
`show` and `remove` did not. Both shipped in ADR-0042 / `1.2.0` as the
missing half of the new-machine / broken-session story behind ACCOUNTS-001.

| Item | Value | Effort | Status |
|---|---|---|---|
| `tg accounts show <alias>` | med | S | graduated — ADR-0042 / 1.2.0 |
| `tg accounts remove <alias>` | low-med | XS | graduated — ADR-0042 / 1.2.0 |

- **`show`** — session path, lock state, and presence of local authorization
  material, *without opening a connection*. Only a live `--connect` probe can
  establish that Telegram still accepts that material after a server-side
  revoke. This is the account-scoped offline branch of `doctor --connect`
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

### Coverage sweep of the wacli pages (2026-07-23)

The first pass covered 26 wacli doc pages (12 in the main review + 14 swept by
two cheap sub-agents), with findings re-checked against the tgcli code. Review
then found two more pages in the current published surface, `calls` and
`companion integrations`; both are included in the dispositions below. Most
pages produced nothing new because their surfaces are already-have or meta:

- **ALREADY-HAVE:** `channels`, `chats` (our `dialog`/`mark-*`), `version`,
  and `help` (argparse gives both). Profile *reads* too:
  `users.getFullUser` and `photos.getUserPhotos` are already read-allowlisted,
  so reading a profile works via `tg api` today. A sibling release branch
  proposes `CHANGELOG.md` + ADR-0038 version discipline, but neither is in this
  branch or `main`; do not count it as current behavior until it lands.
- **SKIP:** `groups` (creation is a moderation-vertical gap, already parked
  there), `contacts-import-system` (platform binding + external name source,
  belongs in `tg-agent`), `install`/`overview`/`quickstart`/`docs` (meta), and
  Homebrew packaging (uv is the deliberate choice — see the Go-vs-Python note).
  `calls` is a WhatsApp-store event log with no equivalent approved Telegram
  call workflow. `companion integrations` recommends JSON/events/read-only
  database access; tgcli already has JSON/TSV and read-only `batch`, while a
  mirrored database is deliberately out of scope.

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
| `tg store stats` / `store cleanup` | med-high | S | **adopted — [ADR-0040](decisions/ADR-0040-wacli-review-adoption-scope.md)** |
| `--events` NDJSON lifecycle stream | med-high | M | **deferred → FEED-001 (ADR-0040)** |
| `tg doctor` offline by default + `--connect` | med | S | **adopted — [ADR-0040](decisions/ADR-0040-wacli-review-adoption-scope.md)** |
| `tg spec --json` | med | S | **deferred — needs overturning ADR-0028 (ADR-0040)** |

Owner decision 2026-07-23 (grilling + domain-modeling session): adopt the two
local/offline items (`store`, `doctor --connect`); defer the two that carry a
forward cost (`--events` is a contract best designed with FEED-001; `tg spec`
requires overturning ADR-0028 and should wait for demonstrated drift pain).
Scope and boundaries in [ADR-0040](decisions/ADR-0040-wacli-review-adoption-scope.md).

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

**`tg doctor --connect`.** Today `doctor` performs a live probe whenever the
session file exists and its lock is free; missing or busy sessions already get
a limited local report. That live authorization check cannot succeed when the
session is revoked or the network is down — precisely when broader local
diagnostics are still needed. wacli splits it: offline by default (store
layout, local auth material, locks), while `--connect` adds live
authorization/connectivity checks. Our offline branch would cover config
validity, file permissions, session presence, lock state, and state size
without claiming server authorization. Natural companion to ACCOUNTS-001,
which needs a diagnosis path that works on a broken session.

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

### Local archive and search — telecrawl-inspired owner scenario (2026-07-30)

The generic local mirror was previously declined because Telegram has a
server-side history and search surface. The owner has now named a different,
concrete scenario: Telegram is the primary communication app, and information
must be searched frequently across private chats, groups, and selected
channels. That makes a local archive a user-facing retrieval layer rather than
an abstract backend replacement.

[`openclaw/telecrawl`](https://github.com/openclaw/telecrawl) validates one
candidate shape: import local Telegram Desktop `tdata` or macOS Postbox data
into a local SQLite archive, keep a `messages` table plus SQLite FTS5, and
expose local search over chats, topics, senders, replies, media metadata, and
dates. It also records observable edits and deletions and keeps ordinary
archive/search commands local; encrypted GitHub backup is explicit rather than
implicit.

This is a proposal, not an approval to add a mirror. `tgcli` remains the live
Telegram control route; a local archive would be read-only and account-scoped.
The first candidate should be an explicit, foreground import for selected
dialogs, not a daemon or an automatic full-account mirror. The Telegram server
remains the source of truth; gaps, incomplete local Desktop/Postbox history,
account changes, edits, and deletions require explicit freshness and
rebaseline semantics. The storage and privacy model must also be chosen before
any message bodies are placed under `~/.local/state/tgcli/`.

| Item | Value | Effort | Status |
|---|---|---|---|
| Selected-dialog local archive + FTS5 search | high | M | **shipped through Phase 6** — filtered/ranked search, offline read/history, bounded refresh, and manual launchd template (ADR-0068/0069/0070) |
| Import/rebuild/status with account identity and gap reporting | high | M | **archive store/search/history/refresh shipped; rebuild/purge remain deferred** (ADR-0068/0069/0070) |
| Continuous event-driven mirror | ? | L | **rejected for this effort — hourly foreground one-shots instead (ADR-0068)** |

Resolution (2026-07-31): wayfinder map #100 worked the gates below to closure
— research #101–#103, decisions #104–#108, ADR-0068 accepted, plan at
`docs/superpowers/plans/2026-07-31-archive-store.md`. The sidecar question is
settled (native store; telecrawl rejected with grounds in #104), and the
Desktop/Postbox-history question is moot: acquisition is server-side via
tgcli's own export/changes surfaces. The remaining gates live on in the plan
as Phase 2's proof-of-value acceptance and the global read-only constraint.

Original re-entry gates (historical):

- compare local results with live `tg search` over a fixed set of real search
  tasks, including private chats and selected channels;
- prove which history is actually available from local Desktop/Postbox data and
  which requires a Telegram API backfill;
- define account binding, permissions, retention, deletion, rebuild, and
  backup rules before storing message text locally;
- choose between a telecrawl sidecar and a native tgcli store without coupling
  the Python control plane to an unreviewed Go binary contract;
- keep imports and local search read-only, with no send/edit/delete/clone
  mutation path through the archive.

---

## Hardening / simplification backlog (2026-07-26 campaign review)

Engineering (non-feature) proposals from the Fable 5 hardening campaign's
whole-project review. Same maintenance-mode gate as everything else here:
each item needs an explicit owner request + ADR + scoped plan before code.
Evidence pointers reference the campaign audit; in-campaign work (error
boundary in `cli.py`, defensive state loading, the `commands/clone.py`
split, peer-id consistency fixes) is tracked in
`docs/handoffs/FABLE5_PROGRESS.md` — do not re-propose it here.

| Item | Value | Effort | Note |
|---|---|---|---|
| Persisted intent records for mutation crash windows | high | M | Generalize the 1.2.16 pin recovery: write intent → RPC → promote, so a crash between a Telegram mutation and its state save is always recoverable. Known candidates: `pin_pending` (source pin moved during the crash window), `topics.create_topic` (create RPC before mapping save), poll vote retract (ADR-0048 invariant). State-schema change → its own ADR; apply only where a defect is proven, not as a framework. |
| Adopt `ruff` rule family `B` (flake8-bugbear) | med | XS | Measured 2026-07-26 on a clean copy of the branch: **7 violations total**, three of them `B023` (a closure in a loop capturing the loop variable — `commands/clone.py` refresh commit, `transfer.py` upload worker). Those are benign today only because every thunk is awaited inside its own iteration; the moment one is deferred or retried later they bind the wrong value. Also `B904` (raise-without-from in `transfer.py`) and `B905` (`zip` without `strict=`). Cheap, high signal, one slice. Measured but **not** worth adopting: `UP` 21, `SIM` 25, `PTH` 37 (pure churn in a maintenance-mode repo), `TRY` 360, `ARG` 840 (noise). |
| Do **not** raise pyright to `strict` | — | — | Measured 2026-07-26: `typeCheckingMode = "strict"` produces **4956 errors**, almost all `reportUnknown*` from Telethon's untyped surface. Recorded so nobody re-litigates it: the honest reading is that `basic` plus the boundary tests AGENTS.md already mandates is the right posture until Telethon ships stubs. |
| Release-tag catch-up (v1.2.10–v1.2.15) | high | XS | ADR-0038 mandates a `vX.Y.Z` tag per release; upstream has them through `v1.2.9`, then stops (issue #72 covers `v1.2.10`; `v1.2.11`–`v1.2.15` have no issue yet). Agent sessions cannot push tags (git proxy denies `refs/tags/*`; re-verified 2026-07-26), so this is owner-local work: tag each release commit or amend ADR-0038. |
| Telethon pin upgrade (1.44.0 → current) | med | L | Single large external dependency; the FEATURES.md namespace matrix and CONTRACT §6 stability exemption bound the blast radius, but an upgrade needs its own project: full gate + live acceptance of clone/login surfaces. Do not bundle with anything. |
| Create secret files at 0600 directly (`os.open`/`O_CREAT`) | med | S | Closes the residual create-then-chmod TOCTOU window on new `.session` / `audit.jsonl` / `invocations.jsonl` files that the 1.2.16 permissions work documented as accepted debt. Pure tightening, no contract impact. |
| Peer-id emission property test | med | S | After the campaign fixes the known raw-id vs `-100…` inconsistencies, add one table-driven test asserting every command's emitted dialog/peer ids follow the same documented convention, so the class of drift (audit af-06/18/19) cannot silently return. |
| `doctor` operational failure probes | low | S | Offline checks users actually hit: disk-full on the state filesystem, clock skew large enough to break ISO comparisons/preview TTLs. Additive `checks` keys only. Feature-adjacent — needs a real incident or owner pull to justify. |

---

## Backend performance and runtime direction (owner discussion, 2026-07-26)

Recorded from an owner-side design discussion held while the 1.2.16
hardening campaign ran. **Nothing here is approved** — the maintenance-mode
gate applies to every row, and the runtime rows additionally need a product
decision from the owner before any ADR is worth writing.

The discussion proposed a full stack: a long-lived per-account runtime, a
job queue, IPC, an event queue, a REST API, a SQLite store, and a
`TelegramBackend` abstraction over Telethon. Recorded in full because the
analysis is good, and split here by what the evidence actually supports.

**Read this framing first.** That stack is, in shape, the daemon-first
architecture tgcli exists to replace (ADR-0002; the AGENTS.md "no daemons"
hard rule). Each element is also a new defect class — daemon lifecycle, IPC
races, queue recovery — in a project that just spent a whole campaign
removing defect classes from a *simple* architecture. So the filter is not
"does this make the backend more mature" but "does a real owner scenario
require it".

### Worth doing, evidence already exists

| Item | Value | Effort | Note |
|---|---|---|---|
| Performance baseline before any optimisation | high | S | No optimisation should land without a before/after table: wall time, Telegram RPC count, and disk writes per scenario (`clone` cold/resume at 1k/10k mappings, `send`, `clone status`, `media download`). `scripts/bench.py` already exists as a starting point. Cheap, behaviour-free, and it is what makes every row below provable instead of plausible. |
| `--profile` structured run report | med | S | Additive JSON on long commands: duration, messages processed, RPC count and retries, flood-wait seconds, bytes moved, state writes, cache hits/misses. Serves humans and agents equally — an agent can tell "slow because Telegram" from "slow because we rewrite state per message". Additive JSON only, no contract break. |
| Per-run entity/RPC cache | med | S | The audit found real repeat work (`get_me` before every clone phase, the same peer resolved repeatedly, `GetFullChannel` more often than needed). A cache scoped to one invocation needs no daemon and no new state: pure win, measurable with the baseline above. |
| Clone-state SQLite/WAL | high | **shipped `1.2.19`** | ADR-0060 delivered the measured prototype, versioned schema, automatic JSON import with `.imported` backup, integrity reporting, duplicate guard, and `clone export-state` rollback path. Small files remain JSON. |

### Needs an owner product decision first

| Item | Value | Effort | Note |
|---|---|---|---|
| Optional per-account runtime (`tg runtime start`) | ? | **L** | One process owns the session and Telethon client; CLI commands are handed to it over a local socket; clone runs as a background job beside interactive commands. This is the third time the idea has surfaced (FEED-001's former lock blocker, the clone/runtime design input in ISSUES.md, now here). It is **a daemon** and contradicts ADR-0002, so it can only enter through an ADR that overturns that line deliberately. Before any of that, one product question decides everything: **does the owner want a continuous 24/7 mirroring clone?** If yes, runtime is the honest foundation. If the real usage is "run `clone sync` when I think of it", none of this floor is needed — ADR-0062 named session roles already keep the primary free during long jobs. |
| Durable job queue (`tg jobs` list/pause/resume/cancel) | ? | L | Only meaningful with a runtime; inherits the same decision. Would need per-job progress, checkpoints, attempt counts, and restart recovery. |
| Event-driven live clone (Telegram updates → idempotent apply) | ? | L | The scenario that actually justifies the two rows above. The ADR-0060 storage prerequisite and ADR-0063 event feed now exist; the remaining work is an explicit product policy per event class — new post, edit, delete, comment, topic, pin — plus runtime ownership. Deletions must be events, not absences. |
| Local REST/IPC API for agents | low | M | Premature: the machine interface already exists (JSON + exit codes + read-only `tg batch`). An HTTP surface adds tokens, rate limits, TLS, and a new authorization model to protect the same session files. Revisit only for genuine remote access, and never expose a general "execute any RPC" endpoint. |

### Considered and declined

| Item | Why not |
|---|---|
| Full `TelegramBackend` abstraction over Telethon | Over-engineering for a swap that will not happen (ADR-0001 chose Telethon deliberately). The narrow, useful half is already tracked above as the pin-upgrade tripwire: collect the private-API uses (e.g. `utils._photo_size_byte_count`) into one place so an upgrade knows where to look. A full interface layer buys indirection, not safety. |
| Rewrite in Rust/Go, microservices, Redis/Kafka/PostgreSQL, a plugin system, supporting several Telegram libraries | The bottleneck is Telegram's own rate limiting, repeated state rewrites, and per-command reconnects — not the language. Every item here adds code and defect classes without touching the measured cost. |
| Raising concurrency as a speed lever | Telegram rate-limits by itself: four workers can beat two while eight simply earn more FloodWait. If concurrency is ever tuned it must be adaptive (back off on flood, recover carefully) and proven with the baseline — not raised as a constant. |

**Suggested order if the owner green-lights the remaining first block:**
baseline and `--profile` → per-run RPC cache → *then* answer the
24/7-mirror question before touching anything in the second block.

---

## Process-speed rule revisions (2026-07-26, PR-history data)

Owner question: changes feel slow — is the test/process discipline worth
revisiting? Answered with measurements over the merged PR history (39
merged PRs), the gate, and shared-file churn, taken on the campaign branch
at `673033a`. Same maintenance-mode gate as everything else: process rules
live in AGENTS.md, so each adopted row lands as an AGENTS.md edit in its
own slice, plus an ADR where the row amends an existing decision.

**What the data rules out.** Neither test runtime nor merge latency is the
cost: the full `./scripts/gate.sh` runs in 52 s wall-clock (pytest 29.5 s
for 1326 tests), and PR cycle time is median **1.2 h** open→merge, p75
3.3 h, max 11.7 h — none over a day. The real per-change cost is volume
plus serialization: recent feature PRs carry ≈2.8 test lines and ≈2
docs/process lines per `src/` line (PR #78: 260 src / 729 tests / 666
docs; PR #77: 349/965/625; PR #74: 254/534/228), and the shared files
conflict by construction — of 133 commits in this branch's history,
`docs/DEVLOG.md` appears in 54, `scripts/check-architecture.py` in 38,
`docs/CONTRACT.md` in 33, `CHANGELOG.md` in 27.

**Not candidates — measured defect catchers.** Red-first tests, boundary
tests, the independent pre-merge review, and the full gate before commit
stay as they are. Their hit rate is the evidence: hardening wave 1 came
back needs-work on 4 of 5 slices, PR #77's review found two confirmed
majors, and 13 of 133 commits are review fixes — while the gate itself
costs 52 s. Weakening these trades a 1.2 h median cycle for the return of
bf-01-class defects.

| Item | Value | Effort | Note |
|---|---|---|---|
| Integrator-assigned version/CHANGELOG at merge | high | S | Revises ADR-0038 **mechanics**, not intent. Evidence the current shape misfires: the 1.2.10/1.2.11 version race between parallel branches, PR #51 (`__version__` drift fix), PR #38 (CHANGELOG finalized after tagging), and tags `v1.2.10`–`v1.2.15` missing because agent sessions cannot push tags. Change: feature branches never touch the version, CHANGELOG, or tag; the integrator assigns the number and writes the CHANGELOG section in the merge that lands the change. Contract changes still never accumulate unreleased — only *who and when* moves. Needs an ADR amending ADR-0038. |
| DEVLOG as per-entry files | high | S | `docs/devlog/YYYY-MM-DD-slug.md`, one file per session entry; `DEVLOG.md` becomes an index or a generated view. Removes the single hottest conflict file (54/133 commits into one newest-on-top file; DEVLOG itself names CHANGELOG/DEVLOG/ceilings as the rebase hotspots) with zero loss of discipline. AGENTS.md edit. |
| Line-ceiling tolerance band | med | S | `check-architecture.py` fails only above ceiling +50 lines (or +10 %); the integrator ratchets ceilings back down at merge. Keeps the anti-bloat control, removes the per-PR ceiling reconciliation that today touches 38/133 commits plus its `tests/test_check_architecture.py` mirror. |
| Parallel waves branch from the integration head | med | XS | All six wave-2 worktrees branched from `main` instead of the campaign head: every cherry-pick needed ceiling reconciliation, and one slice re-invented `DeadlineExceeded` that wave 1 had already landed. One AGENTS.md line: a wave's worktrees start at the integrator's current head. |
| ADR-lite template for XS/S changes | med | XS | 11 full ADRs shipped in ~10 days (0045–0055). A one-page form (decision / rejected alternatives / contract impact) for XS/S slices; the full template stays mandatory for CONTRACT.md changes, safety behavior, and new dependencies. |
| `pytest-xdist -n auto` in the gate | low | XS | 29.5 s → ≈8–10 s on the suite; marginal next to the 52 s total, but free. Dev-only dependency — still gets its ADR row per the AGENTS new-dependency rule if adopted. |

---

## Suggested sequencing (remaining items only)

1. **Local archive/search (M, shipped — ADR-0068 Phase 6):** native store,
   bounded media/transcription, filtered/ranked search, offline read/history,
   one-shot refresh, and manual launchd packaging are complete. Rebuild/purge,
   export bundle, and off-machine backup remain separately scoped.
2. **Data tail (S/M):** `export bundle` and typed temporary/permanent media
   failures, only against a concrete backup workflow.
3. **Measured performance (S):** baseline → `--profile` → per-run RPC cache.
4. **Verticals (M/L, per-scenario ADRs):** community, moderation, stats, and
   security only against concrete demand.
5. **Runtime direction (L):** only after an explicit owner decision that
   continuous 24/7 cloning is a product requirement.
