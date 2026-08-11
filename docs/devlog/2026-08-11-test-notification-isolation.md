## 2026-08-11 — Isolate job notifications in scheduler tests (Codex)
**Did:** reproduced issue #196 with a fake `osascript` harness around the
terminal clone-job scheduler test. Notification Center identified the real
sender as `com.apple.ScriptEditor2`; no persistent clone job or tgcli
LaunchAgent existed. Added a file-scoped autouse fixture that captures
`desktop.notify` in every Telegram scheduler test, and made the terminal
failure test assert the exact redacted title and body.
**Decided:** keep production notification behavior unchanged. This is test
isolation, not a scheduler policy change, so no ADR or CONTRACT update is
needed.
**Learned:** mocked Telegram and temporary state did not make the suite fully
hermetic because the desktop notification seam was still native on macOS.
**Next:** run the focused fake-`osascript` repro, full gate, and independent
whole-diff review before merging.
