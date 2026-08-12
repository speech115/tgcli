## 2026-08-12 — Recoverable archive peer purge (Codex)

**Did:** implemented owner-approved issue #198 as `tg archive purge CHAT`.
The offline default reports exact peer row/file/byte inventory; `--confirm`
quarantines media and resumable downloads, deletes all peer-owned SQLite/FTS
state in one transaction, removes cursor/gap/reconcile traces, then reaps the
quarantine. Added public-boundary tests for preview, commit, private rejection,
readonly, active sessions, queued archive jobs, database failure, and cleanup
failure/retry. `archive remove` remains scope-only.

**Decided:** ADR-0089. Global SQLite/filesystem atomicity is not claimed;
an atomic marker makes both sides idempotently recoverable by the same stored
username or peer id. Confirmed purge keeps audit and jobs history, refuses all
active archive workloads, and never opens Telegram.
The archive-local lock is shared with transcription so a concurrent engine
completion cannot recreate an FTS row after purge.
Archive job creation shares that lock, closing the check-then-enqueue race.
Download checkpoints quarantine below the state downloads root, so an external
archive volume never requires a cross-device rename.
The purge resolver rejects message-only numeric identities, and audit failure
leaves no marker, quarantined file, or deleted row.
It also rejects recycled usernames that match multiple durable peers. Pending
recovery blocks every archive writer/new archive job until retry, so work cannot
recreate a quarantined peer and wedge recovery.
Checkpoint destination now proves account ownership before global download
state is touched. Archive writers take a shared operation lock while purge is
exclusive, closing a late named-role race without serializing the two job
lanes. Headless purge creates only the private lock directory, not a session.
Retry preserves any same-key checkpoint created after the original was moved
to quarantine.

**Learned:** a peer can survive in FTS, transcript queues, download checkpoints,
the changes channel map, a scoped gap, and reconcile output after its scope row
is gone. Treating `scope` as the deletion inventory would leave several paths
that can rediscover or report the peer.

**Next:** independent whole-diff review, full gate, PR CI, patch release, then
show the owner a live preview before any real archive deletion.
