## 2026-08-13 — Authclient FloodWait boundary (Composer)

**Did:** Set `flood_sleep_threshold=0` on `authclient.unauthorized_client`
to match `session._make_client` (ADR-0072), then fixed PR #244 review
findings at the same boundary. The authorization probe now sends an explicit
`updates.GetStateRequest` instead of calling Telethon's lossy helper.
`unauthorized_client` translates every escaping login `FloodWaitError` to
exit 5, including the post-login `get_me` request. Public CLI regressions
prove that a flooded probe or identity read preserves every session file and
login attempt. Removed unrelated publisher formatting from the branch.

**Decided:** Full lane because FloodWait handling and released login behavior
are affected. No new ADR is needed: this restores ADR-0072 decision 2's
visible-flood rule and ADR-0042 decision 7's authorized-session protection.
The documented exit-5 shape is unchanged, so `docs/CONTRACT.md` is unchanged.

**Learned:** Telethon 1.44 `is_user_authorized()` catches every `RPCError`,
including `FloodWaitError`, and caches `_authorized=False`.

**Next:** Re-review PR #244 from its merge base.
