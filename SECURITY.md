# Security Policy

`tgcli` signs in as a real Telegram **user account**. A vulnerability here can
expose message history, contacts, or the session itself — please report
privately first.

## Supported versions

Only the latest released version on `main` is supported. Fixes ship as a new
patch release ([CHANGELOG.md](CHANGELOG.md)); there are no backports.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this
repository → **Report a vulnerability**. That opens a private advisory visible
only to the maintainer.

If private reporting is unavailable to you, open a public issue that says only
that you have a security report and asks for a private channel — **no details,
no reproduction, no logs** in the public thread.

Please include, in the private report: the version (`tg --version`), the exact
invocation, what happens versus what should happen, and the smallest
reproduction you have. Expect a first response within a few days; this is a
single-maintainer project, not a funded program.

## Never include in a report

Redact before sending, and never paste into a public issue or pull request:

- session files or their contents (`~/.local/state/tgcli/*.session`);
- `api_id` / `api_hash`, login codes, 2FA passwords, QR login links;
- phone numbers, and message content from real conversations;
- raw audit logs (`~/.local/state/tgcli/audit.jsonl`) — they name real peers.

A synthetic reproduction on a test account is always preferred over a real one.

## Scope

In scope — anything that lets a local process or a crafted server response:

- read or exfiltrate session material, config credentials, or the audit log;
- bypass the safety gates: `--readonly` / `TGCLI_READONLY`, `TGCLI_NO_SEND`,
  the preview → commit two-step, the `tg api` write gate and denylist;
- send, edit, delete, or forward a message that the user never committed;
- cause a committed preview to be silently replayed or duplicated;
- write outside `~/.config/tgcli/` and `~/.local/state/tgcli/`, or leave those
  paths more permissive than mode `0700`.

Out of scope:

- Telegram platform behavior and MTProto itself — report those to Telegram;
- vulnerabilities in [`telethon`](https://github.com/LonamiWebs/Telethon) —
  report upstream, and tell us so the pin can move;
- an attacker who already has read access to your home directory: a local
  session file is by design enough to act as that account;
- rate limits, FLOOD_WAIT behavior, and account restrictions imposed by
  Telegram for how you use your own account.

## Operational note

`tgcli` never runs in the background. Every command is a foreground process
that exits, so revoking a session in Telegram (Settings → Devices) immediately
and completely cuts this tool off from the account.

Named session roles (ADR-0062) are additional authorized Telegram devices:
each `accounts login --role` creates one more entry in Settings → Devices and
may trigger a login alert. Retiring a role means both `accounts remove ALIAS
--role NAME --confirm` (local file) **and** revoking that device in Telegram;
deleting only the local file leaves the remote authorization intact until it
is revoked or expires.
