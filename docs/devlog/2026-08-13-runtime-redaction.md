## 2026-08-13 — T17 runtime error phone redaction (Cursor)

**Did:** Added shared free-form phone scrubbing to CLI RUNTIME envelopes,
batch per-operation errors, and persisted job errors. Review regressions first
proved that `--verbose` printed the raw exception before the masked envelope
and that `+1 (202) 555-0123` bypassed the compact-number regex. The verbose
path now redacts the formatted traceback before writing stderr, and the helper
accepts common phone separators while still requiring a leading `+`. Fixed two
pre-existing `E501` failures in `scripts/publish-thermos-backlog.py`.

**Decided:** Full lane under
[ADR-0095](../decisions/ADR-0095-runtime-error-phone-redaction.md): this changes
released diagnostics and persisted job error content. `docs/CONTRACT.md`,
version files, and `CHANGELOG.md` remain unchanged because flags, JSON shapes,
and exit codes do not move.

**Learned:** Redacting the final error envelope is insufficient when an earlier
diagnostic renders the same exception, and free-form upstream errors can retain
display punctuation even when tgcli's own phone references are normalized.

**Next:** independent whole-diff Spec + Standards review from the merge-base.
