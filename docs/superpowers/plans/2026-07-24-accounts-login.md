# `tg accounts login` + account lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tgcli able to authorize its own sessions, so that a revoked or
lost `.session` no longer requires a hand-written Telethon script, and close the
account lifecycle (`import`, `login`, `list`, `show`, `remove`). Scope and
boundaries are fixed by
[ADR-0042](../../decisions/ADR-0042-accounts-login.md); vocabulary by the root
[CONTEXT.md](../../../CONTEXT.md).

**Architecture:** Four slices. Slice 1 is offline-only and ships alone
(`accounts show` / `remove`). Slice 2 adds the QR path — the default and the
one that needs no typed secret. Slice 3 adds the phone + code + cloud-password
path with its multi-step state. Slice 4 is hygiene, docs and the `1.2.0`
release. Slices 2 and 3 are tightly coupled and may share one PR; 1 and 4 must
not be folded into them.

**Tech Stack:** Python 3.12, argparse, pytest, Telethon 1.44 (pinned).
**No new dependencies** — in particular no QR-rendering library (ADR-0042
consequences); `osascript` and `open` are invoked as subprocesses.

## Global Constraints

- **ADR-0042 is the approved scope; nothing beyond it.** In particular:
  - **Never write into `sessions/<alias>.session` before Telegram confirms the
    authorization.** Promotion by atomic rename is the only writer. This is the
    load-bearing safety boundary of the whole plan — a failed attempt must be
    incapable of damaging a working session.
  - **The cloud password never reaches `argv`, a log, an audit record, or an
    exception message.** No `--password VALUE` flag exists; do not add one "for
    tests" — tests inject through the secret-channel seam.
  - No `accounts logout`. No localhost web server. No QR-rendering dependency.
- **Gates (`safety.py`):** `login` and `remove --confirm` are blocked by
  `--readonly` / `TGCLI_READONLY=1` with exit 2. `TGCLI_NO_SEND` does **not**
  apply to any command in this plan. `show` is always allowed.
- **stdout is contract data only** (one JSON document under `--json`).
  Instructions, the QR payload for human eyes, progress and warnings go to
  stderr (CONTRACT §2).
- **Contract discipline (AGENTS.md):** any task changing CLI flags or JSON
  shapes updates `docs/CONTRACT.md` **in the same commit**. All JSON here is
  new, so additive.
- **TDD per task:** failing test → minimal code → green → commit. Tests must
  never touch the real state root or the real config — drive everything through
  `TGCLI_STATE_DIR` and `TGCLI_CONFIG` pointed at `tmp_path`. **No test may
  reach the network**; the Telegram client is faked at the seam defined in
  Task 3.
- **Boundary tests (AGENTS.md:72):** every new Telegram call gets a test
  asserting the exact Telethon method and argument types — `send_code_request`
  receives `str`, `sign_in` receives `phone: str, code: str, phone_code_hash:
  str` from stored state (never re-derived), `sign_in(password=…)` is keyword-
  only, `qr_login()` is used for the QR path. Permissive fakes are not proof.
- **Architecture ceilings.** `cli.py` (254), `parser.py` (439),
  `preflight.py` (203), `dispatch.py` (245) currently sit **exactly** at their
  ceilings in `scripts/check-architecture.py`. Each slice raises the ceilings it
  actually exceeds, to the **measured** size after implementation — never with
  headroom — and the new numbers appear as their own line in the PR
  description.
- Gates before every commit: `uv run pytest -q`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run pyright`,
  `uv run python scripts/check-coverage.py`,
  `uv run python scripts/check-architecture.py`,
  `uv run python scripts/check-docs.py`. Quote real output in the PR.
- Do **not** touch `src/tgcli/clone/`.
- Each slice ends with a DEVLOG entry. Tagging and release are the owner's
  action.

---

## Command grammar (fixed by this plan)

```
tg accounts login ALIAS [--phone PHONE] [--api-id N] [--api-hash H]
                        [--force] [--timeout SECONDS] [--qr-format link|text]
                        [--code VALUE|-] [--password-stdin]
tg accounts login --continue LOGIN_ID [--code VALUE|-] [--password-stdin]
tg accounts show ALIAS
tg accounts remove ALIAS [--confirm] [--keep-session]
```

- No `--phone` ⇒ QR path. `--phone` ⇒ phone path.
- `--api-id`/`--api-hash` are required together and only for an alias absent
  from config; passing them for an existing alias is an argparse error.
- `--continue` takes no `ALIAS` (the attempt records it) and rejects
  `--phone`/`--api-id`/`--api-hash`/`--force`.
- `--timeout` default **120** seconds, QR path only.

## JSON shapes (add to `docs/CONTRACT.md`)

Login, terminal success:

```json
{"alias": "main", "method": "qr", "status": "authorized", "next": null,
 "user": {"id": 123, "username": "x", "phone": "+7…89"},
 "session": "/…/sessions/main.session",
 "backup": "/…/sessions/main.session.bak"}
```

Login, step completed but more is needed:

```json
{"alias": "main", "method": "phone", "status": "pending", "next": "code",
 "login_id": "l_…", "expires_at": "2026-07-24T12:00:00+00:00"}
```

`show` (always offline; `authorized` is always `null`, mirroring `doctor`'s
offline convention):

```json
{"alias": "main", "in_config": true, "session": "/…/main.session",
 "exists": true, "bytes": 32768, "modified": "…", "locked": false,
 "backup": "/…/main.session.bak", "authorized": null}
```

`remove`:

```json
{"alias": "x", "config": "removed", "session": "deleted", "backup": "deleted"}
```

## Exit codes (existing set, ADR-0003 — not extended)

| Code | Case |
|---|---|
| 0 | step succeeded, including `"next": "code"\|"password"` |
| 1 | QR wait timed out (state kept; error names the `login_id` to resume) |
| 2 | `--readonly`/`TGCLI_READONLY`; existing session still authorized and no `--force`; `remove` without `--confirm` |
| 3 | invalid code, invalid cloud password, banned number |
| 4 | unknown alias; unknown or expired `login_id` |
| 5 | FLOOD_WAIT, with `retry_after` |

---

## Slice 1 — offline account lifecycle (Tasks 1–2)

Ships alone, touches no network, and gives Slices 2–3 the command used to
verify their results.

### Task 1: `tg accounts show ALIAS`

**Files:**
- Modify: `src/tgcli/commands/accounts.py` (`show_account`, `show_rows`)
- Modify: `src/tgcli/parser.py`, `src/tgcli/dispatch.py`
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_commands_accounts.py`

**Interfaces:**
- `show_account(config: Config, alias: str) -> dict` — pure filesystem
  inspection. Reports config presence, resolved session path, existence, size,
  mtime (ISO-8601 UTC), whether the account lock is currently held, and the
  `.bak` slot. `authorized` is always `null`.
- Lock state is read **without blocking and without stealing the lock**: open
  the `.lock` path, attempt `LOCK_EX | LOCK_NB`, release immediately if it
  succeeded. Never leaves the lock held; never waits.
- An alias absent from config is exit 4, not an empty document.

**Tests:** all four combinations of {in config, not in config} × {session file
present, absent}; `.bak` present and absent; locked and unlocked (hold the lock
from the test); `--plain` rows; exit 4 on unknown alias; no network is possible
(no client import in the code path).

### Task 2: `tg accounts remove ALIAS [--confirm] [--keep-session]`

**Files:**
- Modify: `src/tgcli/commands/accounts.py` (`remove_account`)
- Modify: `src/tgcli/parser.py`, `src/tgcli/dispatch.py`, `src/tgcli/preflight.py`
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_commands_accounts.py`

**Interfaces:**
- Without `--confirm`: report-only, deletes nothing, prints the `--confirm`
  hint to stderr, exits 2. Mirrors `store cleanup` (ADR-0040).
- With `--confirm`: remove the `[accounts.<alias>]` block from `config.toml`,
  delete `<alias>.session` and its `.bak` unless `--keep-session`, leave the
  `.lock` file alone. Config rewrite must preserve unrelated content and
  comments as far as `tomllib`-read/round-trip allows — read, filter, write
  with mode `0600`; never leave a truncated config behind (write to a temp file
  in the same directory, then `os.replace`).
- Refuses with exit 2 if the account lock is currently held (another `tg` is
  using the session).
- If the alias is `default_account`, refuse with exit 2 and say so; the owner
  edits `default_account` first.
- Writes an audit record (`accounts-remove`, alias, what was deleted).

**Tests:** report-only leaves every file in place; `--confirm` removes exactly
the named block and files; `--keep-session`; unrelated accounts and top-level
keys survive; refusal when the lock is held; refusal on `default_account`;
readonly gate; audit record written before deletion and containing no secrets;
config file mode is `0600` after rewrite.

---

## Slice 2 — QR login (Tasks 3–6)

### Task 3: the client seam and the secret channel

Two small modules created before any command uses them, so both are testable
without Telegram and without a GUI.

**Files:**
- Create: `src/tgcli/desktop.py`
- Create: `src/tgcli/authclient.py`
- Modify: `docs/MAP.md`
- Test: `tests/test_desktop.py`, `tests/test_authclient.py`

**`desktop.py` — the platform escape hatch. Nothing else in the project may
call `osascript` or `open`.**

- `ask_secret(title: str, prompt: str, *, hidden: bool) -> str` — tries an
  `osascript` dialog (`display dialog … default answer "" with hidden answer`
  when `hidden`), falls back to reading one line from stdin. Returns the raw
  value; never logs it, never puts it in an exception message.
- `open_url(url: str) -> bool` — `open <url>` on darwin; `False` when
  unavailable.
- `dialog_available() -> bool` — `sys.platform == "darwin"` **and** `osascript`
  is on `PATH`.
- The prompt text is passed to `osascript` via `-e` with the secret **never**
  appearing in any argument; the answer arrives on the subprocess's stdout.

**Tests:** monkeypatched `subprocess.run` asserts the exact argv and that no
secret is ever in it; non-darwin falls back to stdin; `osascript` missing falls
back to stdin; `osascript` non-zero exit (user pressed Cancel) raises a clean
error, not a traceback; `hidden=True` puts `with hidden answer` in the script.

**`authclient.py` — a Telethon client for a session that is *not yet*
authorized.** `session.client()` cannot be reused: it raises `ConfigError` when
`is_user_authorized()` is false, which is precisely our starting condition.

- `async def unauthorized_client(path: Path, api_id: int, api_hash: str)` —
  async context manager. Takes the `.lock` beside `path` exactly as
  `session.client()` does (`LOCK_EX | LOCK_NB`, `ConfigError` when busy),
  connects, yields, and always disconnects so SQLite is committed and closed.
  It performs **no** authorization check.
- `async def probe_authorized(account: Account) -> bool` — connects with the
  account's existing session and returns `is_user_authorized()`. Used by the
  `--force` preflight. Returns `False` on `SessionRevokedError` rather than
  raising.

**Tests:** lock is taken and released; busy lock raises `ConfigError`;
disconnect happens even when the body raises; `probe_authorized` maps
`SessionRevokedError` to `False`.

### Task 4: attempt state (`logins/`)

**Files:**
- Create: `src/tgcli/login_state.py`
- Modify: `docs/MAP.md`
- Test: `tests/test_login_state.py`

**Interfaces:**
- `logins_dir() -> Path` — `state_dir() / "logins"`, created `0700`.
- `create_attempt(alias: str, method: str, *, api_id, api_hash, phone=None) -> dict`
  — mints `l_<token_urlsafe(16)>`, writes `l_<id>.json` at mode `0600` with
  `alias`, `method`, `api_id`, `api_hash`, `phone`, `phone_code_hash: null`,
  `created_at`, `expires_at`. Returns the record.
- `LOGIN_TTL = timedelta(minutes=30)` — deliberately not `PREVIEW_TTL`; it
  bounds a human-paced handshake, not a rendered mutation.
- `load_attempt(login_id) -> dict` — rejects ids not matching `^l_[A-Za-z0-9_-]+$`
  or containing `/`; missing ⇒ `NotFoundError` (exit 4); past `expires_at` ⇒
  `NotFoundError` naming expiry, and the attempt's files are removed.
- `update_attempt(login_id, **fields)` / `discard_attempt(login_id)`.
- `staged_session_path(login_id) -> Path` — `l_<id>.session`.
- `promote(login_id, destination: Path, *, keep_backup: bool) -> Path | None` —
  the **only** writer of `sessions/<alias>.session`. Under the destination's
  `.lock`: if the destination exists and `keep_backup`, `os.replace` it onto
  `<destination>.bak`; then `os.replace(staged, destination)`; then discard the
  attempt's json. Returns the backup path or `None`. Caller must have
  disconnected the client first so SQLite is closed.

**Tests:** id validation rejects traversal; expiry removes files and raises;
mode is `0600`; `promote` is atomic in effect (destination is either the old
file or the new one — assert via a monkeypatched `os.replace` that raises
between the two moves, and that the destination is still intact); backup slot
is single (a second promotion overwrites `.bak`, never `.bak.bak`); `promote`
refuses when the destination lock is held.

### Task 5: `tg accounts login ALIAS` — QR path

**Files:**
- Create: `src/tgcli/commands/login.py`
- Modify: `src/tgcli/parser.py`, `src/tgcli/preflight.py`, `src/tgcli/dispatch.py`
- Modify: `src/tgcli/commands/accounts.py` (extract `_append_config_block` for
  reuse; do not duplicate it)
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_commands_login.py`, `tests/test_cli_login.py`

**Flow:**
1. Resolve the alias. Absent from config ⇒ require `--api-id`/`--api-hash`,
   else exit 3 with a message naming both flags. Present ⇒ reject those flags.
2. If the alias is configured and its session file exists: `probe_authorized`.
   Authorized and no `--force` ⇒ exit 2, message naming `--force`. This probe is
   skipped when the session file is absent.
3. `create_attempt(method="qr")`; open the staged session through
   `unauthorized_client`.
4. `qr = await client.qr_login()`. Emit to **stderr**: the instruction and,
   per `--qr-format`, either the `tg://login?token=…` URL (`link`, default) or
   the bare token payload (`text`). On darwin with `--qr-format link`, also call
   `desktop.open_url(qr.url)`; log to stderr whether it was opened, so a failure
   to open is visible rather than silent.
5. Loop until `--timeout` elapses: `await qr.wait(timeout=min(remaining, until
   qr.expires))`; on `asyncio.TimeoutError` call `await qr.recreate()`, re-emit
   the URL, and continue. On overall timeout: keep the attempt, exit 1, error
   payload carries `login_id`.
6. `SessionPasswordNeededError` ⇒ collect the cloud password through the
   secret channel (Task 6) and `await client.sign_in(password=…)`. If no channel
   is available (headless, no `--password-stdin`), persist the attempt and
   return `{"status": "pending", "next": "password", "login_id": …}` with
   exit 0.
7. On success: disconnect, `promote(...)`, write the audit record, emit the
   terminal JSON.

**Rules:** the audit record is written **before** promotion (fails closed,
ADR-0011) and carries alias, `method`, outcome, masked phone — never the token.
The staged session is never left connected across a return.

**Tests (all with a faked `qr_login`):** happy path promotes and reports;
`--force` required when the existing session is authorized; no probe when no
session file exists; timeout keeps state and exits 1 with `login_id`; token
re-issue calls `recreate` and re-emits; `--qr-format text` prints the payload
and does **not** call `open_url`; 2FA branch reaches the secret channel;
headless 2FA returns `next: "password"` at exit 0; readonly gate; audit record
precedes promotion (assert ordering by making promotion raise); stdout holds
exactly one JSON document and the URL is on stderr.

### Task 6: cloud-password step and `--continue`

**Files:**
- Modify: `src/tgcli/commands/login.py`, `src/tgcli/parser.py`,
  `src/tgcli/preflight.py`
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_commands_login.py`

**Interfaces:**
- `tg accounts login --continue LOGIN_ID [--password-stdin]` reopens the staged
  session, collects the password (dialog, or stdin under `--password-stdin`),
  calls `sign_in(password=…)`, promotes.
- `PasswordHashInvalidError` ⇒ exit 3, attempt **kept** so the human can retry
  without restarting the handshake.
- The password value never enters a variable that is logged, an exception, or
  the attempt json.

**Tests:** dialog path and `--password-stdin` path; wrong password exits 3 and
keeps the attempt; unknown/expired `login_id` exits 4; `--continue` rejects
`--phone`/`--force`/`--api-id`; boundary test asserts `sign_in` is called with
`password` as a keyword argument and nothing else.

---

## Slice 3 — phone + code path (Tasks 7–8)

### Task 7: `--phone` and the code step

**Files:**
- Modify: `src/tgcli/commands/login.py`, `src/tgcli/parser.py`,
  `src/tgcli/preflight.py`
- Modify: `docs/CONTRACT.md`
- Test: `tests/test_commands_login.py`

**Flow:**
1. Same alias/`--force` preflight as the QR path.
2. `create_attempt(method="phone", phone=…)`; `sent = await
   client.send_code_request(phone)`; store `sent.phone_code_hash` via
   `update_attempt`. Return `{"status": "pending", "next": "code", "login_id":
   …, "expires_at": …}`, exit 0.
3. `tg accounts login --continue L --code …` — code resolution order:
   `--code VALUE` → `--code -` (one line from stdin) → secret channel dialog
   (`hidden=False`). Then `sign_in(phone=…, code=…, phone_code_hash=…)` with
   the hash **read from the attempt**, never recomputed.
4. `SessionPasswordNeededError` ⇒ `{"next": "password"}`, exit 0, attempt kept.
   Otherwise promote as in Task 5.

**Error mapping:** `PhoneCodeInvalidError` ⇒ exit 3, attempt kept.
`PhoneCodeExpiredError` ⇒ exit 3, attempt **discarded**, message says to start
again. `FloodWaitError` ⇒ exit 5 with `retry_after`, attempt kept.
`PhoneNumberBannedError` / `PhoneNumberInvalidError` ⇒ exit 3, discarded.

**Tests:** full three-step handshake with a fake client; `phone_code_hash`
round-trips from state into `sign_in` (boundary test on argument types); each
error mapping above, including which of them keep the attempt; `--code -`
reads stdin; no `--code` reaches the dialog; the phone is masked in the audit
record and in `--plain` output; readonly gate on both steps.

### Task 8: masking and audit hardening

**Files:**
- Modify: `src/tgcli/commands/login.py` (or `formatting.py` if a mask helper
  fits there — one home only)
- Test: `tests/test_commands_login.py`

`mask_phone("+79991234589") == "+7…89"`. Applied everywhere a phone leaves the
process: JSON, `--plain`, stderr, audit. Tests cover short numbers, numbers
without `+`, and empty input; a test greps the written audit line to assert no
code, no password and no full number can appear in it.

---

## Slice 4 — hygiene, docs, release (Tasks 9–11)

### Task 9: `store` learns the `logins/` bucket

**Files:**
- Modify: `src/tgcli/commands/store.py`
- Modify: `docs/CONTRACT.md`, `docs/guide/store.md`
- Test: `tests/test_commands_store.py`

`stats` reports `logins` as `{live, expired}` by `LOGIN_TTL`, counting both the
json and its staged session. `cleanup --confirm` reaps **expired** attempts
(json + staged session) and never live ones. `sessions/` and `audit.jsonl`
remain untouchable — a staged session is attempt state, an account session is
not, and the code must make that distinction by directory, not by filename.
`stats` also reports `.bak` files under `sessions/` as their own line, never
deleting them.

**Tests:** live attempt survives `--confirm`; expired attempt and its staged
session are both removed; a `.bak` is reported and never removed; existing
preview buckets are unchanged.

### Task 10: documentation

**Files:**
- Modify: `docs/CONTRACT.md` (all shapes and exit-code rows above)
- Modify: `docs/guide/accounts.md` (login flows, both paths, headless notes)
- Modify: `docs/guide/safety.md` (add login/remove to the `--readonly` table;
  state explicitly that `TGCLI_NO_SEND` does not apply)
- Modify: `docs/guide/store.md`, `docs/guide/doctor.md` (point at `accounts
  show` for the offline, account-scoped view)
- Modify: `SKILL.md` (routing: "the session died / a new machine")
- Modify: `docs/MAP.md` (`commands/login.py`, `login_state.py`, `desktop.py`,
  `authclient.py`)
- Modify: `docs/ISSUES.md` (ACCOUNTS-001 → closed by ADR-0042, keeping the
  backup note, which is still unmet)
- Modify: `docs/PROPOSALS.md` (mark `accounts show`/`remove` graduated)

`scripts/check-docs.py` is fail-closed: every `--flag` and `tg <command>` named
in the guide must exist in the parser. Run it before committing this task.

### Task 11: release `1.2.0`

**Files:**
- Modify: `pyproject.toml`, `src/tgcli/__init__.py` (`1.1.3` → `1.2.0`)
- Modify: `CHANGELOG.md` (new `## [1.2.0]` section + compare link at the
  bottom, following the pattern already there)
- Modify: `scripts/check-architecture.py` (final measured ceilings)

Owner-declared minor (ADR-0038, ADR-0042 §14). Tagging `v1.2.0` is the owner's
action, after merge.

---

## Live acceptance — merge gate, not follow-up (ADR-0042 §13)

Run **against a secondary account** (`recklessou` or `teamsyncsage`), never
`main`, using a throwaway alias so no existing session file is touched:

```bash
tg accounts login tmp-login --api-id <id> --api-hash <hash>
```

1. The `tg://login?token=…` deep link opens the native macOS client and it
   shows a confirmation sheet. **This is the plan's one unproven assumption** —
   if the handler is not registered, record the fact and fall back to
   `--qr-format text` plus scanning from a phone. Either outcome is acceptable;
   silently skipping the check is not.
2. Token re-issue: wait past one expiry without confirming, then confirm — the
   login still completes.
3. `--phone` path end to end on the same account: code arrives, `--continue
   --code` completes, and with 2FA enabled the password step is reached and
   accepted through the native dialog.
4. Wrong code once and wrong password once: exit 3, the attempt survives, the
   retry succeeds.
5. `tg accounts show tmp-login` reports the promoted session; one read command
   (`tg dialogs --limit 1`) works on the new alias.
6. `--force` refusal on a live session, then `--force` succeeding and leaving
   exactly one `.bak`.
7. `tg accounts remove tmp-login --confirm`; terminate the extra device in the
   Telegram app.

Points 1–4 are unprovable by mocks. Record the real output in the PR.

## Review

Independent whole-diff review from the merge-base, in a fresh context, on both
axes (Spec against ADR-0042, Standards against AGENTS.md). Adversarial passes
that are mandatory here: combined and invalid flags on every new command; the
readonly gate on every mutating path; audit-before-mutation ordering; that no
test or code path can put a secret into `argv` or a log; and that
`sessions/<alias>.session` has exactly one writer in the whole diff.
