# Store: local state hygiene

`tg store` inspects and cleans `TGCLI_STATE_DIR` (default
`~/.local/state/tgcli/`). Both subcommands are offline: no config is loaded
and no Telegram session opens
([ADR-0040](../decisions/ADR-0040-wacli-review-adoption-scope.md)).

## Inventory: `store stats`

```bash
tg --json store stats
```

Read-only inventory of the state root by category:

```json
{"previews":{"live":{"count":2,"bytes":120},"expired":{"count":1,"bytes":40},
 "spent":{"count":3,"bytes":90},"pending":{"count":1,"bytes":30}},
 "previews_world_readable":0,
 "logins":{"live":{"count":2,"bytes":80},"expired":{"count":0,"bytes":0}},
 "audit_log":{"bytes":20},"invocations":{"bytes":0},
 "sessions":{"count":1,"bytes":4096},
 "session_backups":{"count":1,"bytes":4096},
 "clones":{"bytes":0},"jobs":{"bytes":0,"db":{"count":0,"bytes":0},
 "wal":{"count":0,"bytes":0},"shm":{"count":0,"bytes":0},
 "states":{},"unreadable":0},"downloads":{"bytes":0},
 "relics":[{"name":"labs","bytes":11}]}
```

`--plain` columns: `category`, `count` (nullable for size-only rows),
`bytes`.

Preview files are classified from each file's stored `expires_at`, not
mtime: `live` = `.json` within its five-minute TTL, `expired` = `.json` past
TTL, `spent` = `.used`, `pending` = `.pending`. `previews_world_readable`
counts preview files with any other-user permission bit set — a legacy
`0644` body from before previews were tightened to `0600`.

Login attempts under `logins/` are classified by a 30-minute TTL; each
bucket counts the attempt json together with its staged session. Expired
attempts are reaped by `store cleanup --confirm`; live ones are not.
`session_backups` reports `sessions/*.session.bak` and is never deleted —
use `tg accounts show` / `tg accounts remove` for the account-scoped view.

**Relics** (`mirrors`, `mirror-lab`, `labs`, `probes`) are directories left
by the removed `tg mirror` surface. `stats` reports them when present and
flags them "remove by hand" — `store cleanup` never touches them.

**Archive** (`archive/<account>/`, ADR-0068) is inventoried by `stats`
(bytes + `archive.db` / WAL / SHM) and is never deleted by cleanup. A custom
`[archive] root` outside the state directory is not counted here.

**Jobs** (`jobs/<alias>/`, ADR-0087) reports registry DB/WAL/SHM bytes, latest
generation counts by state, and unreadable registry count. Cleanup never
deletes a jobs registry.

## Clean up: `store cleanup`

```bash
tg --json store cleanup
```

| Flag | Effect |
| --- | --- |
| `--older-than N` | only artefacts older than N days, or `Nd`/`Nh` (e.g. `7`, `7d`, `12h`) |
| `--include-pending` | also make far-past-TTL `.pending` previews eligible |
| `--confirm` | actually delete; without it, dry-run only |

**Dry-run is the default.** Without `--confirm`, `cleanup` only reports what
it would remove; stderr prints a one-line hint to re-run with `--confirm`.
With `--confirm`, those files are actually deleted, and surviving preview
files are chmod'd to `0600`.

```json
{"removed":[],"would_remove":["p_spent0.used","p_expired.json"],"bytes":130,
 "confirmed":false,"kept":{"audit_log":true,"sessions":true,"relics":["labs"]}}
```

With `--confirm`, `removed` is populated and `would_remove` is empty.
`--plain` rows: `confirmed`, `count`, `bytes`, `files`.

`--older-than` measures age from each preview's stored `expires_at` (mtime
as a fallback when that field is unreadable).

### The deletion boundary

Cleanup's scope is narrow and non-negotiable:

- **Deletes**: spent previews (`.used` — already committed or consumed) and
  expired previews (`.json` whose five-minute TTL has passed).
- **Never deletes the audit log** (`audit.jsonl`). It is the tamper-evident
  record of every mutation; a cleanup command able to erase it would hand
  an agent a way to cover its tracks.
- **Never deletes sessions.**
- **Never deletes the archive store** under `archive/` (ADR-0068).
- **Never deletes a jobs registry** under `jobs/` (ADR-0087).
- **Never deletes a live preview within its TTL** — only expired or spent
  ones are eligible, regardless of `--older-than`.
- **`.pending` previews are protected**: they hold the idempotency
  `random_id` for a mutation that may still be mid-commit. They are
  eligible for removal only with `--include-pending`, and even then only
  once they are far past TTL (`expires_at + PREVIEW_TTL`, i.e. a full TTL
  past expiry, not just past expiry).
- **Relics are reported, never auto-deleted.** `stats` flags them; removing
  them is a manual, one-time `rm`, deliberately kept outside this command's
  scope.

`store cleanup --confirm` mutates local state, so `--readonly` /
`TGCLI_READONLY=1` blocks it with exit 2 before any deletion. Dry-run (no
`--confirm`) is always allowed under those gates. `TGCLI_NO_SEND=1` does
**not** block cleanup — that guard is for Telegram sends, and cleanup
reaches no network.

## See also

- [safety.md](safety.md) — preview TTL, `.json`/`.pending`/`.used` lifecycle, the audit log
- [../CONTRACT.md](../CONTRACT.md) — §5.05 canonical JSON shapes and boundary rules
- [../decisions/ADR-0040-wacli-review-adoption-scope.md](../decisions/ADR-0040-wacli-review-adoption-scope.md) — why cleanup exists and why it stops where it does
