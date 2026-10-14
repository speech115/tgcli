# CONTEXT — tgcli glossary

Canonical vocabulary for this repository. One meaning per term; do not
introduce synonyms for a term defined here. Decisions live in
[docs/decisions/](docs/decisions/); the CLI contract lives in
[docs/CONTRACT.md](docs/CONTRACT.md). This file is vocabulary only — it holds
no spec and no implementation detail.

Created 2026-07-24 during the ACCOUNTS-001 grill; it starts with the account
and authorization vocabulary because that is where the language was fuzziest.
Extend it lazily, when a term actually needs pinning down.

## Accounts and authorization

**account** — one Telegram user that tgcli can act as. Named by an **alias**,
described by a block in `config.toml` (`api_id`, `api_hash`, `session`), and
backed by a session file. An account exists in configuration whether or not it
is currently authorized.

**alias** — the short local name of an account (`main`, `recklessou`). Chosen
by the owner, never by Telegram. The unit `--account` and `TGCLI_ACCOUNT`
select.

**session file** — the SQLite file under `sessions/` holding the authorization
key for one account. It is irreplaceable material, not a cache: nothing else on
disk can reconstruct it, and a lost one costs a full re-authorization.

**authorization** — Telegram's server-side acceptance of a session key. Lives
on Telegram's side, appears in the user's device list, and can be revoked there
without anything local changing. A session file can therefore be intact and
worthless at the same time; only a live probe distinguishes the two.

**revoked session** — a session file whose authorization Telegram no longer
accepts. The originating condition of ACCOUNTS-001.

## Logging in

**login attempt** — one in-progress authorization, identified by a
`login_id`. Owns a **staged session** and, on the phone path, the
`phone_code_hash` binding it to Telegram. An attempt is abandoned, not failed,
until it either promotes or expires.

**staged session** — the half-authorized session file belonging to a login
attempt. It lives apart from `sessions/` precisely so that a failed attempt
cannot damage the authorization the account is currently running on.

**promotion** — moving a staged session into `sessions/<alias>.session` once
Telegram has confirmed the authorization. The single moment at which a login
attempt becomes the account's session; before it, nothing the account depends
on has changed.

**confirmation code** — the short-lived numeric code Telegram delivers when
authorizing by phone number. A credential, but a narrow one: it expires in
minutes and is useless without the login attempt's local state.

**cloud password** — the account's two-factor password, held by Telegram, not
by a device. A permanent credential to the whole account, and the only secret
tgcli asks a human to type. Never an argument, never logged.

**secret channel** — the route by which a human hands tgcli a secret without it
passing through the command line, the shell history, or an agent's context. A
native system dialog where one is available; standard input where none is.

**headless** — an environment with no way to show a human anything: SSH, CI, a
container. The condition that decides whether the secret channel can be a
dialog.

## Local state

**preview** — the pre-commit safety record for a rendered mutation (ADR-0005).
A preview always describes something the owner can read before it happens; a
login attempt has nothing to render and is therefore not a preview.

**audit log** — the append-only trail of mutations, `audit.jsonl` (ADR-0005).
It records that something happened and to which account, never the content of a
secret.

**relic** — a directory left behind by a removed surface, produced by no
current code path (ADR-0040).

## Request governor

**governor** — the per-account pacing and flood layer around every
authenticated request (ADR-0072). It spaces paced request classes
start-to-start and refuses locally while a type is cooling.

**cooldown** — a server-confirmed FloodWait deadline for one account and one
request type, kept in the governor ledger (`governor.db`). Only that type
waits; `doctor` lists the live ones.

**governed sleep** — time spent waiting on pacing or a cooldown. It does not
count against `--timeout` and does count against `--max-runtime`.

## Archive

**scope** — which peers the archive covers: private dialogs as a standing
category, plus the groups and channels added explicitly.

**catch-up** — a bounded read of new messages for one scoped peer after the
update cursor reports activity there. An unfinished one is resumed first by
the next sync.

**gap** — a stretch of updates the cursor could not deliver (e.g.
`differenceTooLong`). Stored as data, never silently papered over.

**rebaseline** — the explicit recovery from a gap: re-initialize the update
cursor and clear the stored gap, without rebuilding history.

## Clone

**leg** — one source → destination stream of a clone: the posts, or the
comments in the discussion group. Each leg has its own cursor and id map.

**discussion group** — the group linked to a channel that holds its comments.
A comment clone links one to the destination channel.

**anchor** — the discussion-group message Telegram auto-forwards from a
channel post; comments thread under it. Anchors are mapped, never copied.

**attribution** — showing who wrote a cloned message: Telegram's own forward
header where it survives, else a `<display name>: ` text prefix.

**reupload cache** — the `clones/<clone_id>-media/` directory holding source
media downloaded for a reupload batch. Kept after a failed send so a retry
does not download again; removed after a successful one.

**roster** — the best-effort participant snapshot of a clone's source channel
and its discussion group, taken at the end of a sync (ADR-0024).
