# ADR-0109: Retain a pending login phone only in a restricted sidecar

Date: 2026-08-13
Status: accepted
Form: full (persistent login-attempt schema and released command behavior)
Amends: [ADR-0042](ADR-0042-accounts-login.md) decision 5 and
[ADR-0088](ADR-0088-phone-only-session-authorization.md)
Closes: thermos audit T08 and the confirmed PR #248 review finding

## Context

ADR-0042 defined `logins/l_<token>.json` without a phone field, but the
implementation persisted the full phone there for the 30-minute attempt TTL.
The first T08 fix moved that value to `l_<token>.phone`. That removed it from
JSON but still introduced persistent plaintext state without a governing
decision, and `store stats` / `store cleanup` did not know the new file existed.

Dropping the value after `auth.sendCode` is not compatible with the released
cross-process `accounts login --continue` flow. Telegram's
`auth.SignInRequest` requires `phone_number`, `phone_code_hash`, and
`phone_code`. Telethon 1.44 keeps its phone-to-hash map only in client memory;
the staged SQLite session retains the authorization key but not the phone.
A masked phone therefore cannot complete the request in a fresh process.

## Decision

1. Login attempt JSON never contains the raw phone. `_write_attempt` rejects a
   record with a `phone` key.
2. After `send_code_request` succeeds, tgcli atomically writes the exact phone
   to `logins/l_<token>.phone`. The file is mode `0600` under the existing
   `0700` `logins/` directory. A failed code request leaves no phone sidecar.
3. The sidecar is attempt state with the same 30-minute lifetime as its JSON
   record and staged session. `--continue` reads it only to build the required
   sign-in request. Promotion, explicit discard, expired-attempt cleanup, and
   code-expiry cleanup delete it with the other attempt files.
4. `store stats` counts the sidecar and its bytes in the attempt's live or
   expired login bucket. `store cleanup` dry-run lists it, and confirmed
   cleanup deletes it. A live attempt remains protected by the same TTL and
   staged-session lock rules as its sibling files.
5. JSON/stdout, plain output, stderr, and audit continue to expose only masked
   phones. Confirmation codes and cloud passwords are never persisted.

## Rejected alternatives

- **Recover the phone from the staged Telethon session.** Telethon does not
  persist it there, and Telegram's sign-in request still requires the literal
  value.
- **Store only the masked phone.** It is suitable for display and audit, but it
  cannot satisfy `auth.SignInRequest.phone_number`.
- **Require `--phone` again on `--continue`.** This would change the released
  grammar and make the login identifier insufficient to continue an attempt.
  The owner requested the narrower T08 repair, not a second public input.
- **Keep the phone in attempt JSON.** JSON is the routinely inspected control
  record; mixing the raw identifier into it contradicts ADR-0042 and makes
  accidental logging or copying more likely.

## Contract impact

`docs/CONTRACT.md` §5 inventory/cleanup text now includes the optional
`l_<token>.phone` attempt file, and §10 documents why it exists, its permissions,
and its deletion boundary. Flags, output shapes, and exit codes do not change.
The integrator supplies release bookkeeping; this feature branch does not edit
version files or `CHANGELOG.md`.
