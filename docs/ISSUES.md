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
