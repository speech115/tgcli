## 2026-08-09 — #96 resolution: runtime-boundary rule + Telethon version alignment

**Did:** owner chose options 1+3 of #96 over 2/4. Added the "tgcli session
runtime boundary" rule to the home-level workspace AGENTS.md (every agent,
every project — the incidents spanned telegram-mirror, mir-pylesos,
`enrich.py`); aligned the machine's user-site Telethon 1.42.0 → 1.44.0
(`pip install --user --break-system-packages`, PEP 668). Verified bare
`python3` now opens real 6-column sessions without the "expected 5, got 6"
crash; the tgcli venv is untouched (1.44.0). Registered option 4 (`tg doctor`
runtime-schema check) as a deferred backlog row in PROPOSALS.md.

**Decided:** no ADR — docs + environment, no repo behavior change. Option 2
(`tg python`) declined as YAGNI (a spelling of `.venv/bin/python`); option 4
deferred as full lane (released-command behavior) with a re-entry gate: only
if incidents recur despite the rule and the version alignment.

**Learned:** the repo's own AGENTS.md rule did not reach agents working in
other projects; the home-level file is the only place every agent reads.

**Next:** close #96 on the tracker after owner confirmation; re-open option 4
only if the incident class returns.
