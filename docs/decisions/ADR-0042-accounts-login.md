# ADR-0042: `tg accounts login` — session (re)authorization, and the rest of the account lifecycle

Date: 2026-07-24
Status: accepted (QR start path removed by ADR-0088; phone continuation
sidecar governed by ADR-0109)

## Context

Owner request (2026-07-24), closing the last deferred item of
[ISSUES.md](../ISSUES.md) ACCOUNTS-001. Per [ADR-0026](ADR-0026-maintenance-mode.md)
a feature needs an owner request plus an ADR; ACCOUNTS-001 was pre-approved as
maintenance-mode work, and this ADR is that gate.

Every tgcli capability stands on authorized `.session` files imported from the
old stack. There is no in-tool way to create one: after a revoke — the 2026-07
incident began exactly that way — recovery means a hand-written Telethon
script. The cheap mitigation ISSUES.md leans on ("keep `~/.config/tgcli/` and
`~/.local/state/tgcli/` in the machine backup") was **verified unmet on the
owner's machine on 2026-07-24**: Time Machine `AutoBackup = 0`, the only
destination fails to mount. Recovery therefore rests entirely on this feature.

Three facts constrain the design and were established by checking, not by
assumption:

- The owner's desktop client is `ru.keepcoder.Telegram` (Telegram for macOS),
  which has no `tdata` store and no supported session extractor. Even for the
  Qt client, converting a desktop auth key to run under our `api_id` is a known
  trigger for server-side termination — the very failure this feature exists to
  repair.
- Telethon 1.44 (pinned) ships `client.qr_login()`, whose `url` is documented as
  a `tg://login?token=…` URI that "when opened by a Telegram application where
  the user is logged in, will import the login token".
- When an agent runs the command, stdin is a pipe. A TTY prompt does not
  degrade in that environment; it simply does not work. Any design that asks a
  human for a secret through stdin alone is unavailable from inside a chat app,
  which is where the owner works.

Terminology is defined in the root [CONTEXT.md](../../CONTEXT.md) — *account*,
*alias*, *session file*, *authorization*, *login attempt*, *staged session*,
*promotion*, *QR login*, *confirmation code*, *cloud password*, *secret
channel*, *headless*. This ADR uses those terms without redefining them.

Prior art reviewed: [wacli](https://wacli.sh/) `auth` — terminal QR by default,
`--phone` fallback, `--qr-format text`, no GUI and no local web server. Its
problem is strictly easier: pairing a linked WhatsApp device requires the user
to type no secret at all, so wacli never had to solve the cloud-password
channel.

## Decision

1. **Two authorization paths: QR by default, phone + code as the fallback.**
   QR is one blocking foreground invocation that holds a connection while the
   token is re-issued on expiry. Blocking is not a daemon; the "no background
   processes" rule is untouched. The phone path exists for the worst case — no
   live client at hand — and therefore ships proven, not on faith.

2. **Secret channel: native dialog first, stdin for headless.** The cloud
   password is collected through an `osascript` hidden-answer dialog, falling
   back to stdin when no dialog is available. This is the first
   platform-specific branch in the project and is accepted deliberately: it is
   what makes recovery possible from inside an agent app, and it guarantees
   structurally that the password never enters `argv`, shell history, `ps`, or
   an agent's context. No localhost web server: a listening socket would be the
   largest new surface in the project, and it reads as a daemon.

3. **Confirmation code and cloud password are treated asymmetrically.** With no
   flag, both are collected through the secret channel. `--code VALUE` is
   additionally permitted as an argument, `--code -` reads stdin; the password
   is never accepted as an argument. The asymmetry follows the threat model: a
   code expires in minutes and is useless without the attempt's local state,
   while the password is a permanent credential to the whole account.

4. **Scope is the whole account lifecycle.** `accounts login` (re-authorizing a
   configured alias, and creating a new one from `--api-id`/`--api-hash`), plus
   `accounts show` and `accounts remove`. Rejected: `accounts logout` —
   server-side `auth.logOut` is irreversible, the Telegram app already
   terminates sessions, and `remove` covers the local half.

5. **A login attempt is not a preview.** Attempt state lives in its own
   `logins/` directory under the state root: `l_<token>.json` (alias,
   `phone_code_hash`, `created_at`) beside `l_<token>.session`, the staged
   session. The `phone_code_hash` is bound to that session's auth key, so the
   staged file must survive the process boundary. A preview describes a
   rendered mutation and carries a `random_id`; a login has nothing to render.

6. **Promotion is the only moment the account's session changes.** The staged
   session moves into `sessions/<alias>.session` by atomic rename, and only
   after Telegram has confirmed the authorization. Writing into `sessions/`
   before confirmation is forbidden: a failed attempt would destroy a working
   authorization, and a half-authorized file there would make `doctor` and
   `accounts show` lie.

7. **A live existing session is protected.** Login probes the current session
   first; if it is still authorized, it refuses with exit 2 and names
   `--force`. On promotion the displaced file is kept in a single
   `<alias>.session.bak` slot, reported by `store stats` and `accounts show`.
   After an overwrite the server-side authorization keeps living while its
   local key is gone forever; one bounded backup slot is the only insurance
   against that being irreversible.

8. **Gates.** `--readonly` / `TGCLI_READONLY` block login — it changes local
   state and the account's device list, and a readonly flag that lets it
   through means nothing. `TGCLI_NO_SEND` does **not** apply: that flag guards
   Telegram *message-producing* mutations (ADR-0005; the same line
   `enforce_local_mutation_allowed` draws for `store cleanup`), and an agent
   that keeps it permanently set must still be able to repair a session.
   Acknowledged strain: `auth.sendCode` does cause Telegram to deliver a code
   to the user's other devices.

9. **Audit, with redaction.** Login writes an audit record (fails closed before
   the mutation, ADR-0005/0011) carrying alias, method (`qr`/`phone`), outcome
   and timestamp. Never the code, never the password; the phone is masked
   (`+7…89`). A cloud password in a plaintext log would be a vulnerability, not
   a style defect.

10. **An incomplete handshake is a success, not an error.** Each step exits 0
    and reports `"next": "code" | "password" | null`. The fixed exit-code set of
    [ADR-0003](ADR-0003-output-contract.md) is not extended: a step that did
    exactly what it was asked is not an error, and reusing exit 3 would make
    "введите пароль" indistinguishable from "this session is dead" for every
    existing caller.

11. **`accounts show` is strictly offline.** Path, mtime, lock state, config
    presence, `.bak` presence — no connection. The live probe already exists
    twice (`doctor --connect`, and login's own result); a third copy would be a
    third place to handle revoke and FLOOD_WAIT identically. An alias absent
    from config is exit 4 (`NotFoundError`), not exit 3: `accounts show|remove`
    look up a named registry entry the way `read` looks up a dialog, whereas
    `--account` on other commands is a config-resolution failure for the
    operating context (`ConfigError`, exit 3 via `resolve_account`).

12. **`accounts remove` is the one command allowed to delete a session file**,
    for the named alias only and behind `--confirm`. This does not weaken
    ADR-0040's boundary: *cleanup* never touches sessions, because cleanup
    reasons about categories; *remove* is explicit, account-scoped and
    owner-driven.

13. **Live acceptance is a merge gate, not a follow-up.** Both paths are proven
    against a **secondary** account through a throwaway alias, which creates a
    second authorization without touching any existing session file; then
    `accounts remove` and terminate the device in the app. `main` is not used:
    the first ever run of new authorization code does not belong on the account
    all work depends on.

14. **Ships as the owner-declared minor `1.2.0`** (ADR-0038 leaves the minor
    digit to the owner). Until now a revoked session made tgcli dependent on an
    external hand-written script; after this the account lifecycle closes
    (`import`, `login`, `list`, `show`, `remove`), which is the statement a
    minor version exists to make.

15. **Telegram Devices identifies every connection as `tgcli`.** Both regular
    and staged-login clients pass a stable `device_model = "tgcli"`, the
    package version as `app_version`, and only the OS family as
    `system_version`. Telethon's architecture-only default (`arm64`) made
    multiple tgcli authorizations indistinguishable during live cleanup and
    caused the owner to terminate the wrong session.

## Consequences

- **The entry layer's ceilings are raised deliberately.** `cli.py`,
  `parser.py`, `preflight.py` and `dispatch.py` all sit exactly at their
  reviewed ceilings in `scripts/check-architecture.py`, so every added line
  fails CI until the numbers move. They are raised to the measured post-
  implementation size, never "with headroom", and appear as their own line in
  the PR description. A per-family parser module was rejected: it would invent
  the pattern "each command family owns its parser" for exactly one family, and
  the next contributor would no longer know where a flag belongs.

- **`store` grows a bucket.** Abandoned login attempts under `logins/` must be
  classified and reaped by `store stats` / `store cleanup`, reusing the existing
  bucket machinery. Without it the feature re-grows exactly the litter ADR-0040
  just cleared. Staged sessions are attempt state, not account sessions, so
  reaping them does not cross ADR-0040's boundary.

- **The deep link is the one assumption still unproven.** Telethon documents
  the URI's purpose, but whether the macOS client registers the `tg://` handler
  and shows a confirmation sheet is observed only on the first live run. The
  fallback is cheap and must exist from the start: print the raw payload
  (`--qr-format text`, borrowed from wacli) so an external renderer or a phone
  can consume it. This is also why no QR-rendering dependency enters a
  one-dependency project.

- **The `.bak` slot holds a live auth key** in a directory `store cleanup` may
  not touch. It is therefore surfaced by `store stats` and `accounts show`,
  so that a file with real access cannot sit on disk unexplained.

- **A rejected design worth recording:** extracting a session from the desktop
  client. It fails on facts (no extractor for the macOS client) before it fails
  on judgement (api_id mismatch invites termination), and pursuing it would
  rebuild the mine ACCOUNTS-001 already stepped on.
