# Safety Model

Every `tg` invocation is either a free read or a gated mutation. This page
covers the gates: the preview→commit handshake, the runtime kill-switches,
and the audit log they all write to.

## Reads are free

`dialogs`, `read`, `search`, `latest`, `message`, `info`, `count`, `resolve`,
`mutual-chats`, `contacts`, `media manifest`, `thread`, `batch`, `draft show`,
`draft list`, and `doctor` never mutate anything and are never blocked by the
gates below.

## Preview then commit

[send](send.md), [edit and delete](editing.md), [forward](forward.md), and
[draft set/clear](drafts.md) are two-step. `--preview` resolves the target and stores a single-use
`preview_id` under `~/.local/state/tgcli/previews/`, valid for **5 minutes**.
`--commit PREVIEW_ID` replays exactly that stored payload — the text, file,
and target cannot change between the two calls.

```bash
tg --json send CHAT "Hello" --preview
```

```bash
tg --json send --commit p_9f3a
```

A preview is single-use: a successful commit consumes it, and it cannot be
replayed. `send` and `forward` previews additionally carry a Telegram
`random_id`, so a commit that fails on a network or runtime error can be
retried with the same `PREVIEW_ID` without risking a duplicate send. Do not
create a new preview to retry a failed commit.

`clone init` uses the same preview→commit mechanism for creating a
destination (see [../CONTRACT.md](../CONTRACT.md)).

## Content-free direct mutations

`mark-read`, `mark-unread`, and the `dialog` subcommands (`pin`, `unpin`,
`archive`, `unarchive`, `mute`, `unmute`) carry no message content, so they
run directly with no preview step. They are still gated and audited exactly
like a commit — see [inbox](inbox.md).

## Blocking a mutation

Three gates stop a mutation before it reaches Telegram:

| Gate | Scope | Blocks |
| --- | --- | --- |
| `--readonly` | this invocation only | every mutation: `send`/`edit`/`delete`/`forward`/`draft` commits, `mark-read`/`mark-unread`, every `dialog` subcommand, `clone init --commit`/`clone sync`, `tg api --write`, **and** local-only `store cleanup --confirm` |
| `TGCLI_READONLY=1` | every invocation in the environment | same set as `--readonly` |
| `TGCLI_NO_SEND=1` | every invocation in the environment | the same Telegram-reaching mutations as `--readonly`, but **not** `store cleanup --confirm` — that command never touches the network, so the no-send guard does not apply to it |

`TGCLI_NO_SEND=1` is the narrower switch: it exists specifically to stop
Telegram sends while still letting local housekeeping (`tg store cleanup
--confirm`) run. `--readonly` and `TGCLI_READONLY=1` are the broader switch:
they block every mutation, local or remote, with no exception.

A block is exit **2**, checked before configuration, session, audit, or any
network work. Retrying the identical command changes nothing — it fails the
same way until the flag or environment variable is removed. See the exit
code table in [../CONTRACT.md](../CONTRACT.md).

## Audit log

Every authorized mutation appends one JSON line to
`~/.local/state/tgcli/audit.jsonl` (or `TGCLI_STATE_DIR/audit.jsonl`) before
the Telegram call, and a second `-result` line after a confirmed success. Each
record carries at least `timestamp`, `action` (the verb, e.g. `send`,
`send-result`, `mark-read`, `dialog-mute`), and `account`, plus action-specific
detail (chat reference, message id, stored `random_id`, and similar).

The log is append-only and mode **0600**. `tg doctor` reports its permissions
as `audit_perms_ok`; `tg store cleanup` reaps stale previews but never
touches `audit.jsonl` — no `tg` command deletes or truncates it. If the
pre-dispatch audit write fails (disk full, permission error), the mutation is
blocked with exit 2 rather than proceeding unaudited: tgcli never performs an
authorized write with no audit record.

## JSON

The mirrored stderr error line for a safety block:

```json
{"error": {"code": "BLOCKED", "message": "mutation blocked by readonly mode"}}
```

## See also

- [inbox](inbox.md) — the content-free mutations this page's gates apply to
- [api](api.md) — the same gates apply to `tg api --write`
- [doctor](doctor.md) — reports preview and audit permission health
- [../CONTRACT.md](../CONTRACT.md) — exit codes and audit field details
- [../decisions/ADR-0005-safety-model.md](../decisions/ADR-0005-safety-model.md)
- [../decisions/ADR-0011-audit-write-failure-policy.md](../decisions/ADR-0011-audit-write-failure-policy.md)
