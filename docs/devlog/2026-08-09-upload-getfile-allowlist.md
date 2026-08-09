## 2026-08-09 — read-allowlist `upload.getFile`

**Did:** added `upload.getFile` to `READ_METHOD_ALLOWLIST`
(`src/tgcli/commands/api.py`) so story media returned by
`stories.getStoriesByID` can be pulled through `tg api`; extended the
reviewed-methods list in `tests/test_cli_api_policy.py` (the allowlist-parity
and TLRequest-resolution tests now cover it); flipped the `upload` row in
`docs/FEATURES.md` from `excluded` to `api`. Minimal fallback of #156; full
story download in `media download` stays out of scope.

**Decided:** reviewed read-only allowlist expansion under the ADR-0010
mechanism, no new ADR (owner classified #156-minimum as the no-ADR fallback).

**Learned:** `upload.getFile` resolves to `functions.upload.GetFileRequest`
in the pinned Telethon (`location` / `offset` / `limit`), a pure read with no
write side effects.

**Next:** full #156 — `/s/<id>` parsing → `stories.getStoriesByID` →
`upload.getFile` with HEVC/h264 selection in `media download` (full lane).
