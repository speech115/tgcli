## 2026-08-13 — Failed FloodWait arm fails closed (Composer)

**Did:** ADR-0090 complexity reset after independent review of PR #243:
sticky `Ledger.remember_cooldown` (with `armed_at`) on arm write failure;
still re-raise live FloodWait so sibling handlers / jobs requeue keep
working; next same-type RPC refuses via RateLimitError. Ledger test
forces a real closed-connection `sqlite3.Error`. CONTRACT §4 notes sticky
exit 5, not a new exit 2. Addresses thermos T02 / #206.

**Decided:** Do not replace FloodWait with PolicyError — that silently
broke reupload/export soft-degrade paths and jobs requeue routing.

**Learned:** Exception-type swaps at the governed `_call` seam need a
mirror-fix audit of every narrow FloodWait catch.

**Next:** Wave A merge after P0 reviews clear.
