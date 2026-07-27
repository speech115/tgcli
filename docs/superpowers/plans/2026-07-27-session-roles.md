# Named session roles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one account hold several independently authorized Telegram
sessions ("roles") so a long job (`clone sync`, `export`, the future
`tg changes --wait`) no longer blocks every other command on the account.
Scope and decisions are fixed by
[ADR-0062](../../decisions/ADR-0062-job-session-role.md) (accepted
2026-07-27): **arbitrary role names** (`primary` reserved), **global
`--session-role` flag**, **no implicit fallback** in either direction.

**Architecture:** A role is one more `.session` file with its own lock:
`<session>@<role>.session` beside the primary
(`src/tgcli/session.py::session_path` grows an optional role; the lock,
mode-0600 repair, and busy semantics are untouched). The flag is parsed
next to `--account` (`src/tgcli/parser.py:40`) and resolved to a path
before the client opens. Role lifecycle rides the existing ADR-0042
account machinery: `accounts login --role`, `accounts show`,
`accounts remove --role`, `doctor`. Second release of the
SQLite → session-roles → `tg changes` sequence.

**Tech Stack:** Python 3.12, argparse, Telethon (login flow reuse only —
no new RPC), pytest. No new dependencies.

## Global Constraints

- **ADR-0062 is the approved scope; nothing beyond it.** No lock-yielding,
  no IPC, no shared session files (fixed points). A role appears **only**
  through an explicit interactive `accounts login --role` — never created
  implicitly by using the flag.
- **No implicit fallback — the attribution guarantee.** `--session-role X`
  with no authorized `X` for that account is exit 2 with the exact
  remediation (`run: tg accounts login <alias> --role X`); the command
  never proceeds on the primary. Symmetrically, no flag means primary,
  even when roles exist.
- **Role names validate like account aliases** (same charset/length rules
  the config layer applies to aliases; see `src/tgcli/config.py`), and the
  casefold collision guard (ADR-0042, `config.py:60`) extends to
  `alias+role` pairs — `Work@Job` and `work@job` are the same file on
  macOS. `primary` (casefold) is rejected with a message saying why.
- **Session files are secrets.** Role files get the same 0600/0700
  treatment (`session.restrict_file`, `ensure_state_dir`); never committed,
  never printed. Login-alert and device-list consequences are documented,
  not hidden.
- **Audit attribution.** Every audit row (`safety.append_audit`) and
  invocation-journal row records the role (absent/`primary` for the
  default) so "which device did this" stays answerable.
- **Contract discipline:** CONTRACT updates (global flag, exit codes, new
  `accounts` surfaces) land in the same commits; integrator owns
  version/CHANGELOG (ADR-0058). Run `./scripts/gate.sh` before every
  commit.

## Slice 1 — role plumbing under the session seam

- [ ] **Role name validation (TDD):** shared helper (config or session
  layer): alias-grade validation, `primary` reserved, casefold rules.
  Adversarial tests: empty, dots/slashes/traversal, unicode homoglyphs,
  `PRIMARY`, 100-char names.
- [ ] **`session_path(account, role=None)` (TDD):** `<session>@<role>.session`;
  update `lock_held` callers; collision guard across alias+role pairs.
- [ ] **`session.client(account, role=None)` (TDD):** missing/unauthorized
  role file → `ConfigError` (exit 2) with the remediation text; busy role →
  the existing busy message naming `alias@role`. The primary path must be
  byte-identical to today when `role is None` (regression pin).
- [ ] **Global `--session-role` flag** in `parser.py` beside `--account`;
  threading through `dispatch.py`/`preflight.py` to every client open.
  Boundary tests: flag accepted by a read command, a mutation command, and
  `tg api`; unknown role exits 2 before any network use.

## Slice 2 — lifecycle: login, show, remove, doctor

- [ ] **`accounts login ALIAS --role NAME` (TDD):** reuses the ADR-0042
  QR/phone flow (`commands/login.py`) targeting the role path; pending
  login-state files keyed by alias+role so two logins can't collide.
- [ ] **`accounts show`:** per-alias role list (name, authorized, lock
  state via `lock_held`); JSON shape additive.
- [ ] **`accounts remove ALIAS --role NAME`:** deletes exactly that role's
  session/lock/backup files, never the primary, never config; two-step
  confirm posture consistent with existing `remove`.
- [ ] **`doctor`:** each role checked like a first-class session
  (existence, mode, lock probe, loose-file warnings —
  `commands/doctor.py:49` pattern).
- [ ] **Audit/journal rows carry the role**; tests assert the field on a
  mutation through a role and its absence/default on the primary.
- [ ] **Docs:** CONTRACT (flag, exit 2 semantics, `accounts` JSON),
  MAP.md, guide page, SECURITY.md note: each role is an authorized device
  in Telegram's list, may trigger login alerts, and must be revoked like
  a device when retired.

## Slice 3 — live acceptance (release gate)

Owner present (login needs QR/phone); test account first; results in the
session devlog. **No tag before this slice is green.**

- [ ] `accounts login <test> --role job` → `accounts show` lists it;
  Telegram's device list shows the new entry.
- [ ] Concurrency proof: long-running command on `--session-role job`
  while `tg read`/`tg send` runs on the primary — both succeed; the same
  pair on one session still yields the busy error.
- [ ] Exit-2 proof: `--session-role nosuch` on a live account exits 2
  with remediation, no network side effects, correct audit row.
- [ ] `accounts remove <test> --role job` removes the file; device revoked
  from Telegram (owner action) and documented in SECURITY.md terms.
- [ ] Report gate + CI output; integrator merges, bumps version/CHANGELOG,
  tags after acceptance.
