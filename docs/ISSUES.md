# Deferred Issues

This file tracks deliberately deferred product work that should survive the
current implementation plan. Items here are not promises for the current
release.

## CLONE-001 — Poll cloning

**Status:** partially completed by ADR-0019 after clone v1 live acceptance.

`tg clone sync` now emits a truthful static snapshot instead of skipping a poll.
A controlled live native-forward canary proved that Telegram assigns a new poll
ID and resets non-zero source results to zero, so native forwarding cannot
preserve the requested historical result snapshot.

Before implementation, decide and document the fidelity contract:

- a recreated native poll cannot preserve original votes or voters;
- closed/open state, quiz answers, explanations, anonymity, and multiple-choice
  behavior need explicit live verification;
- the destination must keep a visible one-for-one position even when exact
  reconstruction is impossible (for example, by using a documented fallback).

Open single-choice and multiple-choice snapshots are covered by mocked tests and
live evidence from two real polls. Remaining follow-up: controlled closed-poll
and quiz fixtures, including correct-answer and explanation fidelity. This item
does not block clone.

## CLONE-002 — Forum topics and legacy groups

**Status:** closed by ADR-0022 (2026-07-16).

Forum megagroups clone into forum-megagroup destinations with a 1:1 lazy
topic map; live legacy basic groups clone like megagroups (broadcast
destination, attributed transport); bot dialogs are accepted dialog sources.
Migrated or deactivated basic groups are rejected with pointer messages.
Still out of scope: cloning into pre-existing groups, secret chats,
topic edit/close propagation.

## ACCOUNTS-001 — `tg accounts login`: session (re)authorization

**Status:** deferred. **Re-entry trigger:** the first revoked or lost
session on a configured account.

Every tgcli capability stands on authorized `.session` files imported from
the old stack (phase 6). There is no in-tool way to create or re-create
one: if Telegram revokes a session (the 2026-07 incident started exactly
this way) or the file is lost, recovery today is a manual Telethon script.
A recovery path is most needed at the moment it is least convenient to
build, which is why this item is pre-approved as maintenance-mode work
(ADR-0026): when the trigger fires, `tg accounts login <alias>` — an
interactive phone-code/2FA authorization that writes the session file into
the standard location under the standard lock — is in scope and needs an
ADR at implementation time (auth flow touches safety surface).

Until then the cheap mitigation is operational, not code: keep
`~/.config/tgcli/` and `~/.local/state/tgcli/` inside the machine backup
so a disk failure does not mean re-authorizing every account.
