# ADR-0088: Phone-only session authorization

Date: 2026-08-11
Status: accepted (owner request in #193)
Amends: [ADR-0042](ADR-0042-accounts-login.md) by removing its QR start path;
the staged-session, phone/code/password, audit, promotion, account lifecycle,
and named-role rules remain in force.

## Context

The owner reported that the QR login handoff does not appear in the actual
Codex/macOS workflow and explicitly requested its removal from the project,
leaving authorization by phone number. The QR path adds token export, native
URL opening, refresh timing, a long foreground wait, QR-only continuation
state, and a public format flag without providing a usable owner workflow.
The existing phone flow already provides a bounded staged handshake: request a
code, continue with the code, then continue with the cloud password only when
Telegram requires it.

## Decision

`tg accounts login ALIAS --phone PHONE` is the only way to start session
authorization, including a named `--role`. A start invocation without
`--phone`, or with an empty value, is exit 2 before audit, attempt creation,
or network work.

The public `--qr-format` flag and every QR implementation seam are removed:
there is no `qr_login()` call, login-token output, `tg://login` opening,
token recreation loop, QR timeout, or QR password continuation. Existing
pending QR attempts receive no compatibility path and expire through the
normal login-attempt cleanup boundary.

The existing continuation remains unchanged for phone attempts:

```text
tg accounts login ALIAS --phone PHONE [--api-id N --api-hash H]
  [--force] [--role NAME]
tg accounts login --continue LOGIN_ID --code VALUE|-
tg accounts login --continue LOGIN_ID --password-stdin
```

The confirmation code and cloud password remain absent from audit, invocation
journal, stdout, argv recommendations, and persistent attempt JSON. Phone
numbers remain masked in audit and user-facing progress. `--code` and
`--password-stdin` stay continuation-only. Removing the QR wait also removes
the login-specific 120-second default; the ordinary global hang detector
applies to the one-request phone start.

## Rejected alternatives

- Repair the macOS QR deep-link handoff: the owner asked to remove QR, and a
  repaired handoff would retain the unused token lifecycle and public flag.
- Render a terminal QR: Codex does not provide a reliable camera-facing
  terminal workflow, and this preserves the same unusable authorization mode.
- Prompt for a phone number implicitly: a hidden prompt makes automation and
  audit reproduction less explicit. `--phone` is already established and
  keeps the start invocation deterministic.
- Keep `--qr-format` as an ignored compatibility flag: the repository contract
  forbids compatibility layers for removed unreleased direction, and accepting
  a no-op security-sensitive flag would mislead operators.

## Contract impact

This is a breaking removal from a released command. Parser grammar, preflight,
login command code, JSON examples, README/SKILL/account guide, CONTRACT §1/§10,
MAP, tests, and the release notes change together. It ships in the already
owner-approved 3.0.0 integration release alongside ADR-0087.
