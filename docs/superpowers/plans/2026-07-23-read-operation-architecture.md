# Shared read-operation architecture plan

Spec: GitHub #23. Tickets: #24 → #25.
Decision: ADR-0034.

## Slice 1 — deepen the seam (#24)

- Characterize interactive and batch read behavior at `cli.main`.
- Add a closed typed representation for the thirteen shared read operations.
- Prepare operations in each adapter and execute them in one deep module.
- Preserve adapter-specific validation, output, error, and exit behavior.
- Run focused read and batch tests plus Ruff and Pyright.

## Slice 2 — ratchet and verify (#25)

- Add an architecture ownership and no-growth checker to CI.
- Update ADR, MAP, and DEVLOG; leave CONTRACT unchanged.
- Run independent whole-diff Spec and Standards review from `7f7cd26`.
- Fix every confirmed defect test-first.
- Run the complete uv-managed repository gate, then commit, push, PR, CI, and
  merge without Telegram mutations.
