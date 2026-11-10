import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY

import pytest
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

from tgcli import safety
from tgcli.errors import PolicyError


@pytest.mark.parametrize(
    ("readonly", "environment"),
    [
        (True, {}),
        (False, {"TGCLI_READONLY": "1"}),
        (False, {"TGCLI_NO_SEND": "1"}),
    ],
)
def test_mutation_kill_switches_raise_policy_error(monkeypatch, readonly, environment):
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(PolicyError):
        safety.enforce_mutation_allowed(readonly)


def test_preview_expires_after_five_minutes_and_finished_commit_is_single_use():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    preview = safety.create_preview({"chat": "@alice", "text": "hello"}, now=now)

    assert preview["preview_id"].startswith("p_")
    assert preview["expires_at"] == "2026-07-10T12:05:00+00:00"
    assert safety.begin_commit(preview["preview_id"], now=now) == {
        "chat": "@alice",
        "text": "hello",
    }
    safety.finish_commit(preview["preview_id"])
    with pytest.raises(PolicyError, match="already used or does not exist"):
        safety.begin_commit(preview["preview_id"], now=now)


def test_create_preview_writes_mode_0600():
    preview = safety.create_preview({"chat": "@alice", "text": "secret"})
    path = safety.previews_dir() / f"{preview['preview_id']}.json"
    assert path.stat().st_mode & 0o777 == 0o600


def test_commit_transitions_keep_mode_0600():
    preview = safety.create_preview({"kind": "send", "text": "hi"})
    safety.begin_commit(preview["preview_id"])
    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    assert pending.stat().st_mode & 0o777 == 0o600
    safety.finish_commit(preview["preview_id"])
    used = safety.previews_dir() / f"{preview['preview_id']}.used"
    assert used.stat().st_mode & 0o777 == 0o600


def test_expired_preview_is_blocked():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    preview = safety.create_preview({"chat": "@alice", "text": "hello"}, now=now)

    with pytest.raises(PolicyError, match="expired"):
        safety.begin_commit(preview["preview_id"], now=now + timedelta(seconds=301))


def test_begin_commit_allows_retry_until_finished():
    preview = safety.create_preview({"kind": "send", "text": "hi"})

    payload = safety.begin_commit(preview["preview_id"])

    assert payload["text"] == "hi"
    # Network failed mid-send: begin again succeeds with the same payload.
    assert safety.begin_commit(preview["preview_id"])["text"] == "hi"
    safety.finish_commit(preview["preview_id"])
    with pytest.raises(PolicyError):
        safety.begin_commit(preview["preview_id"])


def test_begin_commit_expected_kind_preserves_a_mismatched_preview():
    preview = safety.create_preview({"kind": "clone-init", "source": "@source"})

    with pytest.raises(PolicyError, match="preview does not match send"):
        safety.begin_commit(preview["preview_id"], expected_kind="send")

    preview_path = safety.previews_dir() / f"{preview['preview_id']}.json"
    assert preview_path.exists()
    assert safety.begin_commit(preview["preview_id"]) == {
        "kind": "clone-init",
        "source": "@source",
    }


def test_begin_commit_revalidates_expected_kind_for_a_pending_preview():
    preview = safety.create_preview({"kind": "clone-init"})
    safety.begin_commit(preview["preview_id"])

    with pytest.raises(PolicyError, match="preview does not match send"):
        safety.begin_commit(preview["preview_id"], expected_kind="send")

    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    assert pending.exists()


def test_begin_commit_enforces_ttl_and_id_shape():
    preview = safety.create_preview({"kind": "send"})
    late = datetime.now(UTC) + timedelta(minutes=6)

    with pytest.raises(PolicyError):
        safety.begin_commit(preview["preview_id"], now=late)
    with pytest.raises(PolicyError):
        safety.begin_commit("p_missing")
    with pytest.raises(PolicyError):
        safety.begin_commit("../etc/passwd")


def test_begin_commit_leaves_an_expired_fresh_preview_as_json():
    """An expired preview must never become sticky-pending (T13): default
    `store cleanup` skips .pending outright, so a preview that was never
    begun must stay a plain .json to land in the expired bucket cleanup
    already reaps without --include-pending."""
    preview = safety.create_preview({"kind": "send"})
    late = datetime.now(UTC) + timedelta(minutes=6)

    with pytest.raises(PolicyError, match="expired"):
        safety.begin_commit(preview["preview_id"], now=late)

    previews_dir = safety.previews_dir()
    assert (previews_dir / f"{preview['preview_id']}.json").exists()
    assert not (previews_dir / f"{preview['preview_id']}.pending").exists()


def test_begin_commit_returns_an_expired_pending_preview_to_json():
    """A retried begin_commit landing after the TTL must not leave the
    preview sticky-pending either (T13): it is renamed back to .json so
    it joins the same expired bucket, instead of waiting on
    --include-pending for a second full TTL."""
    preview = safety.create_preview({"kind": "send"})
    safety.begin_commit(preview["preview_id"])
    late = datetime.now(UTC) + timedelta(minutes=6)

    with pytest.raises(PolicyError, match="expired"):
        safety.begin_commit(preview["preview_id"], now=late)

    previews_dir = safety.previews_dir()
    assert (previews_dir / f"{preview['preview_id']}.json").exists()
    assert not (previews_dir / f"{preview['preview_id']}.pending").exists()


def test_begin_commit_handles_pending_preview_disappearing_during_read(monkeypatch):
    preview = safety.create_preview({"kind": "send"})
    safety.begin_commit(preview["preview_id"])
    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    original_read_text = type(pending).read_text

    def vanish_before_read(path, *args, **kwargs):
        if path == pending:
            pending.unlink()
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(pending), "read_text", vanish_before_read)

    with pytest.raises(PolicyError, match="already used or does not exist"):
        safety.begin_commit(preview["preview_id"])


def test_finish_commit_rejects_invalid_preview_id_before_filesystem_access(monkeypatch):
    def unexpected_filesystem_access():
        raise AssertionError("finish_commit should validate before filesystem access")

    monkeypatch.setattr(safety, "previews_dir", unexpected_filesystem_access)

    with pytest.raises(PolicyError, match="already used or does not exist"):
        safety.finish_commit("../etc/passwd")


def test_audit_appends_one_json_object_per_line():
    safety.append_audit("send", "main", {"preview_id": "p_test"})
    safety.append_audit("api", "main", {"method": "messages.sendMessage"})

    lines = safety.audit_path().read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "action": "send",
            "account": "main",
            "preview_id": "p_test",
            "timestamp": ANY,
        },
        {
            "action": "api",
            "account": "main",
            "method": "messages.sendMessage",
            "timestamp": ANY,
        },
    ]


def test_audit_write_error_is_a_policy_block(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "audit_path", lambda: tmp_path)

    with pytest.raises(PolicyError, match="cannot write audit record"):
        safety.append_audit("send", "main", {"preview_id": "p_test"})


def test_append_audit_creates_0600_file_in_0700_state_root(wide_umask):
    safety.append_audit("send", "main", {"preview_id": "p_test"})

    path = safety.audit_path()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_append_audit_repairs_a_loose_existing_file(wide_umask):
    path = safety.audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.chmod(path, 0o666)

    safety.append_audit("send", "main", {"preview_id": "p_test"})

    assert path.stat().st_mode & 0o777 == 0o600
    assert len(path.read_text().splitlines()) == 1


def test_append_audit_chmod_failure_is_fail_open(monkeypatch):
    """Permission repair is protection, not a new failure mode."""

    def deny(path, mode, *args, **kwargs):
        raise PermissionError("chmod denied")

    monkeypatch.setattr("tgcli.session.os.chmod", deny)

    safety.append_audit("send", "main", {"preview_id": "p_test"})

    assert len(safety.audit_path().read_text().splitlines()) == 1


def test_create_preview_directory_is_0700_under_wide_umask(wide_umask):
    safety.create_preview({"chat": "@alice", "text": "hello"})

    assert safety.previews_dir().stat().st_mode & 0o777 == 0o700


def test_begin_commit_rejects_a_naive_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    directory = safety.previews_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "p_naive2.json").write_text(
        json.dumps({"payload": {"kind": "send"}, "expires_at": "2026-07-26T12:00:00"})
    )

    with pytest.raises(PolicyError):
        safety.begin_commit("p_naive2")


class CommitLockMachine(RuleBasedStateMachine):
    """The preview -> commit handshake as one-shot and held (tg-serve) runs see it.

    An invocation begins a commit, may finish it, and always ends. Another tg
    process probes the `.pending` flock: it must be blocked exactly while an
    invocation holds an unfinished commit, and a finished preview stays spent.
    """

    def __init__(self):
        super().__init__()
        self.preview_id = safety.create_preview({"kind": "send", "text": "hi"})[
            "preview_id"
        ]
        self.in_invocation = False
        self.finished = False

    @rule()
    def begin(self):
        if self.finished:
            with pytest.raises(PolicyError, match="already used"):
                safety.begin_commit(self.preview_id)
        else:
            assert safety.begin_commit(self.preview_id)["text"] == "hi"
        self.in_invocation = True

    @precondition(lambda self: self.in_invocation and not self.finished)
    @rule()
    def finish(self):
        safety.finish_commit(self.preview_id)
        self.finished = True

    @rule()
    def end_invocation(self):
        safety.release_commit_locks()
        self.in_invocation = False

    @invariant()
    def other_process_sees_the_lock_only_while_a_commit_is_open(self):
        pending = safety.previews_dir() / f"{self.preview_id}.pending"
        try:
            fd = os.open(pending, os.O_RDONLY)
        except FileNotFoundError:
            assert self.finished or not self.in_invocation
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = False
        except BlockingIOError:
            blocked = True
        finally:
            os.close(fd)
        assert blocked == (self.in_invocation and not self.finished)

    def teardown(self):
        safety.release_commit_locks()


test_commit_lock_machine = CommitLockMachine.TestCase
test_commit_lock_machine.settings = settings(
    max_examples=50, stateful_step_count=10, deadline=None
)
