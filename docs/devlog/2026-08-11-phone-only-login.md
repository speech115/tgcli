## 2026-08-11 — Phone-only session authorization (Codex)
**Did:** implemented owner request #193 and ADR-0088 on
`codex/issue-193-phone-login`. Made `--phone PHONE` mandatory and non-empty
for every login start, including named roles. Removed `--qr-format`, the
Telegram QR token/wait/recreate path, native `tg://login` opening, QR timeout,
and QR-only tests. Kept the staged phone → code → optional cloud-password
continuation, masked output/audit data, atomic promotion, backups, and role
isolation. Updated CONTRACT, SKILL, account/jobs guides, FEATURES, MAP, ADR
index, architecture ratchets, and regression coverage. Full gate: 1876 passed,
9 skipped; ruff, format, strict architecture, pyright, coverage, and docs green.
**Decided:** removed pending QR attempts have no compatibility path; continuing
one fails before a Telegram client opens and the operator starts again with a
phone number. Login start now uses the ordinary 60-second hang detector, while
`--continue` keeps no implicit deadline so human code/password entry is not
preempted.
**Learned:** the parser removal alone was insufficient: preflight also had to
reject a missing or empty phone before audit, attempt creation, or network I/O.
**Next:** independent whole-diff review, then merge #193 into the #146 campaign
branch and perform the owner-assisted live phone authorization.
