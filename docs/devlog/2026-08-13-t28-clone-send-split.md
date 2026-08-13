# 2026-08-13 — T28 clone send execution split

**Did:** Moved `forward_batch` and `reupload_batch` from `commands/clone.py`
into new `clone/send.py`. Command module keeps sync glue calling
`clone_send.forward_batch`. Ratcheted clone.py ceiling 1296→1060; added
send.py ceiling 290. ADR-0112.

**Gate:** `./scripts/gate.sh` green on branch `cursor/clone-send-split-1ec8`.

**Contract:** none.
