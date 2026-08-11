## 2026-08-11 — Telegram jobs lane

**Did:** implemented #188's three Telegram workload adapters: one incomplete
archive dialog, one changes difference with bounded catch-up/media, and one
50-batch clone window. Added the explicit-role Telegram runner, registry user
binding, independent lane lock, and uniform top-level `remaining` results.

**Decided:** every Telegram runner is mutation-safe and blocked by readonly or
no-send before config/session/state work. It never falls back to primary. Rate
limits defer without consuming a failure attempt; durable progress before a
later runtime error resets the consecutive-failure streak.

**Learned:** job grammar and validation belong inside the jobs package, while
archive quantum/progress logic belongs in a command-owned companion module.
This keeps the root parser, preflight, CLI, and archive surface within their
reviewed architecture grace bands.

**Next:** independent review and squash #188 into the #146 integration branch;
then #189 adds recurrence/notification, removes archive refresh, performs the
non-publishing live acceptance, and closes the 3.0.0 cutover.
