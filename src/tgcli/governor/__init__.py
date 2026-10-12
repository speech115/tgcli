"""Account-wide Telegram request governor (ADR-0072).

The governor is one seam around Telethon's private ``_call`` plus a persisted
ledger. It keys everything on the **Telegram request type**, never on the peer:
the incident that produced it ran unpaced across 791 peers because every guard
the project had was scoped to something narrower than the account.

* ``registry`` — which request type belongs to which paced class;
* ``gate`` — the ``_call`` wrapper: refuse or sleep while cooling, pace, arm;
* ``ledger`` — the persisted cooldowns every process reads;
* ``pacing`` — per-process intervals, governed sleep, the wall-clock cap.
"""
