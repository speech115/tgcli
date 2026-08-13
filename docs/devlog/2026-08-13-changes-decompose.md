## 2026-08-13 — changes.py poll/wait decompose (thermos T35)

**Did:** Collapsed duplicated wait/settle loops in `commands/changes.py` into
`_poll_interval`, `_wait_poll`, and `_merge_once_doc`; shared difference event
mapping via `_map_difference_events`. Seeded architecture ceiling at 556 lines
(`scripts/check-architecture.py` + test mirror). No CONTRACT change; no new
module — ceiling-only devlog, no ADR.

**Tests:** `uv run pytest tests/test_cli_changes.py tests/test_check_architecture.py -q`;
full `./scripts/gate.sh`.

**Next:** Optional follow-on from T35 ticket — canonical `resolve_entity` for
read/search/thread.
