# Deferred Issues

This file tracks deliberately deferred product work that should survive the
current implementation plan. Items here are not promises for the current
release.

## CLONE-001 — Poll cloning

**Status:** deferred until after clone v1 live acceptance.

`tg clone sync` currently skips Telegram polls and reports each skipped source
message in `skipped_unsupported`. Revisit native poll reconstruction after the
core text, media, album, reply, and protected-content path is live-proven.

Before implementation, decide and document the fidelity contract:

- a recreated poll cannot preserve original votes or voters;
- closed/open state, quiz answers, explanations, anonymity, and multiple-choice
  behavior need explicit live verification;
- the destination must keep a visible one-for-one position even when exact
  reconstruction is impossible (for example, by using a documented fallback).

Acceptance requires mocked contract tests plus a controlled live comparison of
an open poll, a closed poll, and a quiz. This item does not block clone v1.
