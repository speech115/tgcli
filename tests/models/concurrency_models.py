"""Exhaustive BFS explicit-state models for tgcli concurrency & retry machines.

Two finite-state models check every reachable interleaving (not random samples)
and return shortest-path counterexamples in plain code terms when an invariant
breaks. Each model carries named past-bug toggles so tests prove the model
reproduces and rejects every real historical bug before trusting a pass.

Model 1 (login_promote):
  Covers src/tgcli/login_state.py (promote), src/tgcli/commands/login.py
  (continue_login), and src/tgcli/commands/setup.py (SetupFlow._finish_authorized,
  _cancel, _watch_qr).

Model 2 (preview_commit):
  Covers src/tgcli/safety.py (begin_commit, mark_attempted, was_attempted,
  finish_commit, release_commit_locks), src/tgcli/commands/send.py
  (commit, commit_forward), and src/tgcli/chatref.py (resolve_input).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ModelResult:
    states_explored: int
    violations: dict[str, tuple[str, ...]]


def check_bfs[S](
    initial: S,
    step: Callable[[S], Iterable[tuple[str, S]]],
    invariants: list[tuple[str, Callable[[S], bool]]],
    *,
    max_states: int = 100_000,
) -> ModelResult:
    """Explore every reachable state breadth-first; record shortest trace per
    broken invariant."""
    visited: set[S] = {initial}
    queue: deque[tuple[S, tuple[str, ...]]] = deque([(initial, ())])
    violations: dict[str, tuple[str, ...]] = {}
    while queue:
        state, path = queue.popleft()
        for name, predicate in invariants:
            if name not in violations and not predicate(state):
                violations[name] = path
        if len(violations) == len(invariants):
            return ModelResult(len(visited), violations)
        for label, nxt in step(state):
            if nxt in visited:
                continue
            visited.add(nxt)
            if len(visited) > max_states:
                violations["STATE_LIMIT_EXCEEDED"] = path + (label,)
                return ModelResult(len(visited), violations)
            queue.append((nxt, path + (label,)))
    return ModelResult(len(visited), violations)


@dataclass(frozen=True)
class LoginState:
    p1_pc: str = "qr"
    p2_pc: str = "ready"
    p1_backup: str | None = None
    p2_backup: str | None = None
    session_owner: str | None = None
    backup_owner: str | None = None
    lock_holder: str | None = None
    config_owner: str | None = None
    p1_cancelled: bool = False


def verify_login_promote(*, bug: str | None = None) -> ModelResult:
    """Model two concurrent logins (P1=tg setup with QR/cancel, P2=accounts login)
    racing on the same new alias.

    Past-bug toggles:
      - "no_backup_on_new" (PR #516): promote omits backup for a new alias.
      - "unlock_before_config" (PR #516 Codex P2): promote releases session lock
        before config write and re-acquires it to rollback.
      - "cancel_without_recheck" (PR #515 Cursor High): QR scan completes after
        cancel without re-checking step == "qr".
    """
    keep_backup = bug != "no_backup_on_new"
    hold_lock_over_config = bug != "unlock_before_config"
    recheck_cancel = bug != "cancel_without_recheck"

    def step(s: LoginState) -> list[tuple[str, LoginState]]:
        out: list[tuple[str, LoginState]] = []

        if s.p1_pc == "qr":
            out.append(
                (
                    "P1:user_clicks_cancel",
                    replace(s, p1_pc="cancelled", p1_cancelled=True),
                )
            )
            out.append(("P1:qr_scanned", replace(s, p1_pc="qr_scanned")))

        if s.p1_pc == "qr_scanned":
            out.append(
                (
                    "P1:user_clicks_cancel_mid_handoff",
                    replace(s, p1_pc="cancelled_mid", p1_cancelled=True),
                )
            )
            out.append(("P1:enter_promote", replace(s, p1_pc="ready")))

        if s.p1_pc == "cancelled_mid":
            if recheck_cancel:
                out.append(
                    (
                        "P1:watcher_sees_cancelled_and_aborts",
                        replace(s, p1_pc="cancelled"),
                    )
                )
            else:
                out.append(
                    (
                        "P1:watcher_ignores_cancel_and_promotes",
                        replace(s, p1_pc="ready"),
                    )
                )

        for actor in ("P1", "P2"):
            pc = s.p1_pc if actor == "P1" else s.p2_pc
            my_bak = s.p1_backup if actor == "P1" else s.p2_backup

            def set_actor(
                st: LoginState,
                new_pc: str,
                new_bak: str | None = my_bak,
                who: str = actor,
            ) -> LoginState:
                if who == "P1":
                    return replace(st, p1_pc=new_pc, p1_backup=new_bak)
                return replace(st, p2_pc=new_pc, p2_backup=new_bak)

            if pc == "ready" and s.lock_holder is None:
                out.append(
                    (
                        f"{actor}:acquire_session_lock",
                        set_actor(replace(s, lock_holder=actor), "locked"),
                    )
                )

            if pc == "locked" and s.lock_holder == actor:
                had = s.session_owner
                bak = had if (had is not None and keep_backup) else None
                nxt = replace(
                    s,
                    session_owner=actor,
                    backup_owner=bak if bak is not None else s.backup_owner,
                    lock_holder=actor if hold_lock_over_config else None,
                )
                out.append(
                    (f"{actor}:promote_session", set_actor(nxt, "promoted", bak))
                )

            if pc == "promoted":
                if s.config_owner is None:
                    nxt = replace(s, config_owner=actor, lock_holder=None)
                    out.append((f"{actor}:append_config_ok", set_actor(nxt, "done")))
                else:
                    if hold_lock_over_config:
                        restored = my_bak if my_bak is not None else None
                        nxt = replace(
                            s,
                            session_owner=restored,
                            backup_owner=None if my_bak is not None else s.backup_owner,
                            lock_holder=None,
                        )
                        out.append(
                            (
                                f"{actor}:config_conflict_rollback_under_lock",
                                set_actor(nxt, "failed", None),
                            )
                        )
                    else:
                        out.append(
                            (
                                f"{actor}:config_conflict_need_rollback",
                                set_actor(s, "need_rollback"),
                            )
                        )

            if pc == "need_rollback" and s.lock_holder is None:
                restored = my_bak if my_bak is not None else None
                nxt = replace(
                    s,
                    session_owner=restored,
                    backup_owner=None if my_bak is not None else s.backup_owner,
                    lock_holder=None,
                )
                out.append(
                    (
                        f"{actor}:rollback_reacquired_lock",
                        set_actor(nxt, "failed", None),
                    )
                )

        return out

    def committed_session_intact(s: LoginState) -> bool:
        if s.config_owner is None or s.lock_holder is not None:
            return True
        return s.session_owner == s.config_owner

    def cancelled_never_commits(s: LoginState) -> bool:
        if not s.p1_cancelled:
            return True
        return s.session_owner != "P1" and s.config_owner != "P1"

    def lock_released_at_quiescence(s: LoginState) -> bool:
        terminal = {"done", "failed", "cancelled"}
        if s.p1_pc in terminal and s.p2_pc in terminal:
            return s.lock_holder is None
        return True

    return check_bfs(
        LoginState(),
        step,
        [
            ("CommittedSessionIntact", committed_session_intact),
            ("CancelledNeverCommits", cancelled_never_commits),
            ("LockReleasedAtQuiescence", lock_released_at_quiescence),
        ],
    )


@dataclass(frozen=True)
class PreviewCommitState:
    preview_file: str = "json"
    flock_holder: str | None = None
    attempted: bool = False
    expired: bool = False
    source_edited: bool = False
    peer_drifted: bool = False
    telegram_seen_random_id: bool = False
    deliveries: tuple[tuple[str, bool], ...] = ()
    w1_pc: str = "idle"
    w2_pc: str = "idle"
    w1_peer: str = "previewed"
    w2_peer: str = "previewed"
    w1_content_ok: bool = True
    w2_content_ok: bool = True
    expired_commit_attempted: bool = False
    lost_reply_retry_blocked: bool = False


def verify_preview_commit(*, bug: str | None = None) -> ModelResult:
    """Model two concurrent commit workers plus expiry, source edit, peer drift,
    and lost replies for a single preview_id.

    Past-bug toggles:
      - "no_commit_flock" (PR #404): two workers commit the same preview at once.
      - "sticky_expired_pending" (PR #217): expired .pending stays pending.
      - "skip_attempted_marker" (PR #504 Codex P2): retry after lost reply runs
        content check and blocks instead of letting random_id dedupe.
      - "unpinned_peer" (PR #518 Codex P1): commit delivers to drifted peer.
    """
    use_flock = bug != "no_commit_flock"
    expire_pending = bug != "sticky_expired_pending"
    use_attempted = bug != "skip_attempted_marker"
    pin_peer = bug != "unpinned_peer"

    def step(s: PreviewCommitState) -> list[tuple[str, PreviewCommitState]]:
        out: list[tuple[str, PreviewCommitState]] = []

        if not s.expired:
            out.append(("env:ttl_expires", replace(s, expired=True)))
        if not s.source_edited:
            out.append(("env:source_message_edited", replace(s, source_edited=True)))
        if not s.peer_drifted:
            out.append(("env:target_peer_drifts", replace(s, peer_drifted=True)))

        for worker in ("W1", "W2"):
            pc = s.w1_pc if worker == "W1" else s.w2_pc
            w_peer = s.w1_peer if worker == "W1" else s.w2_peer
            w_content_ok = s.w1_content_ok if worker == "W1" else s.w2_content_ok

            def set_w(
                st: PreviewCommitState,
                new_pc: str,
                *,
                peer: str = w_peer,
                content_ok: bool = w_content_ok,
                who: str = worker,
            ) -> PreviewCommitState:
                if who == "W1":
                    return replace(
                        st, w1_pc=new_pc, w1_peer=peer, w1_content_ok=content_ok
                    )
                return replace(st, w2_pc=new_pc, w2_peer=peer, w2_content_ok=content_ok)

            if pc == "idle":
                if s.preview_file == "used":
                    out.append((f"{worker}:begin_sees_used", set_w(s, "done")))
                elif s.preview_file == "json":
                    if s.expired:
                        out.append(
                            (f"{worker}:begin_sees_expired_json", set_w(s, "done"))
                        )
                    else:
                        nxt = replace(
                            s,
                            preview_file="pending",
                            flock_holder=worker if use_flock else None,
                        )
                        out.append(
                            (f"{worker}:begin_moves_to_pending", set_w(nxt, "locked"))
                        )
                elif s.preview_file == "pending":
                    if not use_flock or s.flock_holder is None:
                        if s.expired:
                            nxt = replace(
                                s,
                                preview_file="json" if expire_pending else "pending",
                                attempted=False if expire_pending else s.attempted,
                                flock_holder=None,
                                expired_commit_attempted=True,
                            )
                            out.append(
                                (f"{worker}:begin_expires_pending", set_w(nxt, "done"))
                            )
                        else:
                            nxt = replace(
                                s, flock_holder=worker if use_flock else s.flock_holder
                            )
                            out.append(
                                (f"{worker}:begin_locks_pending", set_w(nxt, "locked"))
                            )

            if pc == "locked":
                resolved = "drifted" if s.peer_drifted else "previewed"
                if pin_peer and resolved == "drifted":
                    nxt = replace(
                        s,
                        flock_holder=None
                        if s.flock_holder == worker
                        else s.flock_holder,
                    )
                    out.append((f"{worker}:refuse_drifted_peer", set_w(nxt, "done")))
                else:
                    out.append(
                        (f"{worker}:peer_verified", set_w(s, "peer_ok", peer=resolved))
                    )

            if pc == "peer_ok":
                skip_content_check = use_attempted and s.attempted
                if not skip_content_check and s.source_edited:
                    blocked_recovery = s.telegram_seen_random_id
                    nxt = replace(
                        s,
                        flock_holder=None
                        if s.flock_holder == worker
                        else s.flock_holder,
                        lost_reply_retry_blocked=s.lost_reply_retry_blocked
                        or blocked_recovery,
                    )
                    out.append((f"{worker}:refuse_edited_content", set_w(nxt, "done")))
                else:
                    nxt = replace(s, attempted=True if use_attempted else s.attempted)
                    checked_ok = True if skip_content_check else (not s.source_edited)
                    out.append(
                        (
                            f"{worker}:mark_attempted_and_send",
                            set_w(nxt, "sending", content_ok=checked_ok),
                        )
                    )

            if pc == "sending":
                delivered = s.deliveries
                if (not s.telegram_seen_random_id or not use_flock) and len(
                    delivered
                ) < 2:
                    delivered = (*delivered, (w_peer, w_content_ok))
                nxt_ok = replace(
                    s,
                    telegram_seen_random_id=True,
                    deliveries=delivered,
                    preview_file="used",
                    attempted=False,
                    flock_holder=None if s.flock_holder == worker else s.flock_holder,
                )
                out.append(
                    (f"{worker}:telegram_ok_finish_commit", set_w(nxt_ok, "done"))
                )

                nxt_lost = replace(
                    s,
                    telegram_seen_random_id=True,
                    deliveries=delivered,
                    flock_holder=None if s.flock_holder == worker else s.flock_holder,
                )
                out.append(
                    (f"{worker}:reply_lost_release_lock", set_w(nxt_lost, "idle"))
                )

        return out

    def at_most_once_delivery(s: PreviewCommitState) -> bool:
        return len(s.deliveries) <= 1

    def exact_intent(s: PreviewCommitState) -> bool:
        return all(
            peer == "previewed" and content_ok for peer, content_ok in s.deliveries
        )

    def no_sticky_pending_after_expiry(s: PreviewCommitState) -> bool:
        if s.expired_commit_attempted:
            return s.preview_file != "pending" and not s.attempted
        return True

    def retry_recovers_after_lost_reply(s: PreviewCommitState) -> bool:
        return not s.lost_reply_retry_blocked

    def no_leaked_flock(s: PreviewCommitState) -> bool:
        if s.w1_pc in ("idle", "done") and s.w2_pc in ("idle", "done"):
            return s.flock_holder is None
        return True

    return check_bfs(
        PreviewCommitState(),
        step,
        [
            ("AtMostOnceDelivery", at_most_once_delivery),
            ("ExactIntent", exact_intent),
            ("NoStickyPendingAfterExpiry", no_sticky_pending_after_expiry),
            ("RetryRecoversAfterLostReply", retry_recovers_after_lost_reply),
            ("NoLeakedFlock", no_leaked_flock),
        ],
    )
