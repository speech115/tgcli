"""Account-wide Telegram request governor (ADR-0072).

The governor is one seam around Telethon's private ``_call`` plus a persisted
ledger. It keys everything on the **Telegram request type**, never on the peer:
the incident that produced it ran unpaced across 791 peers because every guard
the project had was scoped to something narrower than the account.

Modules land in dependency order (see
``docs/superpowers/plans/2026-08-02-account-request-governor.md``):

* ``registry`` — which request type belongs to which paced class (phase 0);
* ``seam`` — the ``_call`` wrapper and its fail-fast pin (phases 0 and 2);
* ``probe`` — the self-verifying probe for recorded cooldowns (phase 3).
"""
