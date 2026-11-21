# ADR-0129: Telegram client, pacing and transfer decisions

Status: Accepted

## Context

These decisions about the Telethon client layer: sessions, pacing, flood and error families, deadlines and chunked transfers used to live as code comments beside the
code they explain. ADR-0132 removes comments from code, so the non-obvious
ones move here, grouped by module and by the function, class or name they
sit on. Each entry is the reason for a choice that the code alone does not
show; the code is the authority for what it does.

## Decision

### `src/tgcli/authclient.py`

- `unauthorized_client`: A state-root parent (sessions/ or logins/) is tgcli's property: create and mode-repair it like every other confirmed site.
- `unauthorized_client`: Login may disconnect before promote so SQLite releases the staged file; a second disconnect would recreate an empty DB at the old path and raise (no entities table) after the session was moved.
- `probe_authorized`: Telethon's is_user_authorized catches every RPCError, including FloodWaitError, and caches False. Probe with its underlying authorization-required request so transient failures cannot authorize replacement of a live session.

### `src/tgcli/client.py`

- `Client._on_login`: Telethon's own login minus updates.GetState/GetDifference.
- `Client._update_loop`: With pushes off, the queue still gets the updates that ride on RPC results (sends, deletes, read marks). Telethon would check them for gaps and fetch differences; nothing here reads them, so drop them.

### `src/tgcli/deadline.py`

- `GRACE`: The deadline is armed before preflight, so it must not preempt a command that owns a graceful asyncio deadline around the network. Those arm later; this margin keeps them first.
- `bounded`: The command's own deadline (transcribe's wait_for) may still win inside the grace window and raise its detailed CommandTimeoutError; fall back to the generic TIMEOUT only once that window is over. The SIGALRM backstop is disarmed first so its `seconds + grace` firing cannot race this wait.
- `bounded`: Past the deadline, a builtin TimeoutError (a socket timing out in the grace window) is the deadline too.
- `bounded`: Let its `finally` blocks run before the next step does: asyncio.run waited for them, a held loop (tg-serve) would not.

### `src/tgcli/loop.py`

- `run`: A deadline signal or a kill landed while waiting: the coroutine must not outlive the step that started it, so cancel it and, as asyncio.run would, wait until its cleanup has run.

### `src/tgcli/pace.py`

- `FLOOD_SLEEP_THRESHOLD`: Telethon sleeps a FloodWait up to this long and sends the request again; a longer one raises, and the command exits 5 with `retry_after`. It also remembers the wait per request type for the rest of the process: a further request of that type sleeps out what is left, or raises unsent. `Pace.call` resends a write after a flood of up to this long as well.
- `PAGE_WAIT_S`: Between the pages of one history or enumeration walk, and between the peers of a loop that walks many. The 2026-07-31 flood was 791 dialogs read with no gap at all.
- `INTERACTIVE_PAGE_WAIT`: A command that reads one chat at the owner's request. `None` is Telethon's own default: no wait up to 3000 messages, 1 s a page beyond.
- `SEND_INTERVAL_S`: 20 sends a minute, the pace a clone held without a flood.
- `Pace.wait`: Reserved before the sleep, so concurrent tasks queue behind this call instead of computing the same wait.

### `src/tgcli/session.py`

- `session_path`: Defense in depth (T07): load_config already rejects an escaping stem, but an Account built directly (a future caller, a test double) must not be able to make this resolve outside sessions/ either.
- `BUSY_WAIT_SECONDS`: How long a command waits for a session another tg process holds. Agents run in parallel; failing at once only made them retry (618 exit 3s in 30 days).
- `_NO_RESEND`: A mutation is never retried blind: a timeout or a 500 leaves its outcome unknown. Telethon has no setting that resends after a flood only, and with no retries it would sleep the flood and raise it anyway, so a session that may write raises a flood at once. The call site that knows a send is safe to repeat resends it once through `pace.Pace.call`.
- `_make_client`: An authorized session never needs pushed updates (see `Client`); login keeps them, since sign-in flows may wait on one.
- `client`: A second client would wait on the lock the holder keeps.

### `src/tgcli/tg_errors.py`

- `FLOOD_ERRORS`: Telegram's 420 families that say "wait N seconds" about the account, not the request: FLOOD_WAIT_X, and FLOOD_PREMIUM_WAIT_X, the transfer speed limit a non-Premium account meets. Both carry `.seconds` and both exit 5. Catch a flood through this tuple, never through one class.
- `TRANSIENT_ERRORS`: "Ask again later", never "Telegram refused". `raise_last_call_error=True` makes a call whose retries ran out raise its own error; without it Telethon raises ValueError, the same type `get_entity` uses for "no such peer", and callers turned one 500 into permanent state. Code that maps refusals to a lasting outcome (a marker, a fallback, a cached miss) re-raises these first.
- `RUN_ABORTING_ERRORS`: Errors about the run, never about the one item it was working on: a flood, a connection Telethon could not restore, a session Telegram no longer accepts. A loop that records a per-item failure (an attempt counter, an `error` field, a skipped peer) re-raises these before its `except Exception`, or one outage marks every item it touched.
- `NOT_REFUSALS`: Never a refusal about one peer: "ask again later", or a session Telegram no longer accepts, which a re-login fixes. A catch site that maps an RPCError to a lasting per-peer outcome re-raises these first.

### `src/tgcli/transfer.py`

- `CLONE_TRANSFER_PARALLEL`: Chunk requests in flight for one large file, on one DC connection. Official clients keep at least this many going; more only brings floods sooner.
- `_run_workers`: Surface the first worker cause so FloodWait keeps the exit-5 path.
- `CHECKPOINT_EVERY_CHUNKS`: One checkpoint per this many chunks. Frequent enough that a killed run loses seconds of transfer, rare enough that the sidecar write is noise against the bytes (the same cadence `media download` has always used).
- `download_resumable`: 0600 from the first byte: the partial file is the user's media.
- `download_resumable`: Bookkeeping must never replace the exception being unwound: a FloodWait that leaves as an OSError loses its `retry_after`, and with it the exit-5 contract (CONTRACT §4) (review finding). Losing the last few chunks is the cheaper failure — the next run re-fetches them.
- `download_resumable_windowed`: A checkpoint inside a chunk cannot seed the stride; stay serial.
- `download_resumable_windowed`: Bytes past the prefix are unproven; drop them so the part file and its checkpoint agree.

## Consequences

A change that breaks one of these reasons updates or removes its entry in the
same PR. A new decision of this kind is added here, not as a code comment.
