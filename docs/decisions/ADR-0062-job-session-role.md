# ADR-0062: A second authorized session for long jobs

Date: 2026-07-26
Status: accepted (2026-07-27, owner decisions recorded below)

## Context

`session.client()` takes an exclusive lock per session file and holds it
for the whole invocation. A long `tg clone sync` therefore makes every
other command on that account fail with "session is busy" for the run's
duration, and the FEED-001 poller shape (`tg changes --wait N`) would own
the lock essentially 100% of the time. The same contention has now
surfaced three times independently (FEED-001 blocker, the clone/runtime
design input in ISSUES.md, the 2026-07-26 backend discussion). Two fixed
points from ISSUES.md constrain any answer: one session file shared by
concurrent processes stays forbidden (SQLite corruption risks the
authorization itself), and no daemons (ADR-0002).

## Proposal

Named session **roles** per account. The primary session keeps its
current path; a job session is a separate file and a separate Telegram
authorization:

- `tg accounts login ALIAS --role job` authorizes the role. Same QR/phone
  flows as ADR-0042; the session file lands beside the primary as
  `ALIAS@job.session` with its own lock. Telegram lists it as one more
  device — that is the honest, documented cost.
- Long-running commands (`clone sync`, `clone refresh`, `export`, bulk
  `media download`, a future `tg changes`) accept `--session-role job`;
  everything else defaults to the primary. No implicit fallback in either
  direction: a missing role is exit 2 with a pointer to `accounts login
  --role`, never a silent switch — otherwise "which device did this" is
  unanswerable in the audit log.
- `accounts show` reports the roles that exist; `doctor` checks each
  role's session/lock/permissions like a first-class session; `accounts
  remove --role job` deletes one role without touching the primary.
- Audit and invocation-journal rows record the role so mutations stay
  attributable to a specific authorization.

## Owner decisions (2026-07-27)

Accepted with two amendments to the proposal above:

- **Role names are arbitrary**, not a fixed `job`. `accounts login ALIAS
  --role NAME` accepts any name passing the same validation as account
  aliases; the file is `ALIAS@<role>.session`. `primary` is reserved
  (it would be a second name for the default session). Rationale: the
  lock is per file, so one role is one concurrent lane — the target
  scenario "a clone runs while `tg changes` polls" already needs two.
  `job` remains the documented convention for the common case. Each
  role is one more authorized Telegram device; roles can only appear
  through an explicit interactive login, never implicitly.
- **`--session-role` is a global flag** (beside `--account`), available
  to every command, not only long-running ones. It resolves to a
  session path before the client opens; authorization, permissions, and
  audit attribution are identical either way, and "primary is busy, let
  me `tg send` via a role" is a legitimate escape hatch. The
  no-implicit-fallback rule is unchanged and is the real attribution
  guarantee: a named role that is not authorized is exit 2, never a
  silent switch to the primary.
- Ships as its own plan, branch, and release (second in the
  SQLite → session-roles → `tg changes` sequence); tagged only after
  live acceptance on a test account.

## Rejected

- **Yielding the lock between clone windows / `--wait` cycles:** makes
  "busy" a timing lottery, costs a reconnect per window, and interacts
  with cooldown state (both objections already recorded under FEED-001).
- **A session-owner runtime with IPC:** a daemon; contradicts ADR-0002
  and the AGENTS.md hard rule. Only an ADR that overturns that line
  deliberately could introduce it, and no owner scenario currently
  requires 24/7 operation.
- **Sharing one session file with finer-grained locking:** forbidden
  fixed point (authorization-destroying corruption risk).

## Consequences

- The account lifecycle (login/show/remove/doctor) grows a role
  dimension — one scoped plan, its own release, live acceptance with a
  test account before any real one.
- FEED-001's blocker dissolves: the poller runs on the job role while
  the primary stays free for interactive commands.
- Two authorizations mean Telegram may deliver login alerts per role and
  counts both against the account's device list; `SECURITY.md` and the
  guide must say so plainly.
