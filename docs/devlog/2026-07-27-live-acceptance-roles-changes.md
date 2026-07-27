## 2026-07-27 — Live acceptance: session roles + tg changes (Cursor Grok)

**Did:** live-accepted ADR-0062/0063 on test account `vermassov` from
`cursor/tg-changes-cc3b` via `uv run tg` (PATH `tg` is still 1.2.18/main).

Session roles:
- phone login `accounts login vermassov --role job --phone …` →
  `vermassov@job.session`; `accounts show` lists `roles:[{name:job,…}]`
- missing role `--session-role nosuch` → exit **3** `CONFIG` + remediation
  (CONTRACT taxonomy; plan's informal "exit 2" was wrong)
- concurrency: primary `dialogs` OK while `vermassov@job` lock held;
  same role → busy exit 3 naming `vermassov@job`
- `--wait` on `--session-role job` while primary `send` — no lock conflict

`tg changes` on disposable owned channel `[Clone] Пылесос`
(`-1004495133881`):
- `--init --peer` baselined; send → `message_new`; edit → `message_edit`;
  delete → `message_delete` tombstone `{ids:[44]}`
- unsubscribed peer surfaced as `channel_activity`; `--drop-peer` /
  `--peer` mid-stream worked
- `--wait 20` on job role received events while primary sent; settle
  caught 1/2 of a tight burst (second on next poll) — honest gap vs ideal

**Live bug found + fixed:** ChannelDifference yields patched `Message`
with `.text is None` while TL `.message` holds the body;
`message_to_dict` now falls back. Re-proved live: `message_new` text
`"body-fix-alive"`.

**Decided:** keep `vermassov@job` for now; owner still revokes the device
in Telegram Settings → Devices when retiring the role
(`accounts remove vermassov --role job --confirm` locally).

**Learned:** QR login on this Mac is unreliable → phone+code path for
role auth; gap `differenceTooLong` not forced live (mock coverage stands).

**Next:** commit the body-text fix onto the changes PR; owner confirms
device-list entry; integrator merges #93 then #94 after final OK.
