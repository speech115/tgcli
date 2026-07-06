# ADR-0006: Telethon media first, TDLib as optional fallback backend

Status: superseded by ADR-0009 (2026-07-06) — re-audit of the old stack's
records showed the "TDLib proved reliable" claim was never benchmarked; the
incident root causes were operational. See ADR-0009 for the corrected story.

## Context
The old stack proved (2026-06, private-channel download work) that some
media — notably in restricted/private channels — downloads reliably via
TDLib where Telethon paths failed, and that MCP's 120s cap made any
long download impossible. The PoC lives in
`tools/telegram/experiments/tdlib-media-poc`.

## Decision
`tg media download` uses Telethon streaming by default (no artificial
timeout, progress to stderr). A TDLib backend in `src/tgcli/backends/tdlib.py`
is opt-in via `--backend tdlib`, ported from the PoC in phase 3. Core tgcli
must install and run without TDLib present.

## Consequences
- No hard TDLib dependency; heavy path stays optional.
- The known private-channel failure mode has a documented escape hatch.
