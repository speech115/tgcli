## 2026-08-13 — Authclient flood_sleep_threshold=0 (Composer)

**Did:** Set `flood_sleep_threshold=0` on `authclient.unauthorized_client`
to match `session._make_client` (ADR-0072). Tests in `test_authclient`.
Addresses thermos T20 / #224.

**Decided:** Small-fix lane — restores existing ADR-0072 policy on the
login constructor; no CONTRACT change.

**Learned:** none.

**Next:** Continue thermos P2 backlog.
