## 2026-08-13 — Clone JSON→SQLite import is crash-safe (T06) (Cursor Composer)

**Did:** fixed `clone/statedb.py::finish_json_import` + `clone/state.py::load`
(ADR-0101). Import now renames `.json` to `.json.importing` before the
SQLite write, not `.imported` after it; `load()` resumes an in-progress
`.importing` marker instead of hitting the both-files "manual resolution
required" `PolicyError`. Two new tests in `tests/test_clone_state.py`
inject an `os.replace`/`statedb.persist` failure to simulate a crash on
each side of the SQLite write and assert the next `load()` self-heals.

**Decided:** ADR-0101 (full lane — persistent state file schema/behavior,
ADR-0073 trigger 3). CONTRACT §11 gained one clause naming the marker.

**Learned:** `login_state.py::promote` has a similar two-rename shape but
already restores on exception within the same call; it is not
crash-(SIGKILL)-safe either, but that gap is a separate ticket, not a T06
mirror-fix (different subsystem, different existing safety net).

**Next:** none pending for T06.
