# tg api — raw passthrough

`tg api` calls a raw Telegram TL method by name. Use it only when no wrapped
command covers the task — prefer `read`, `send`, `dialog mute`, and friends
whenever one exists; they carry the JSON contract and safety review this
escape hatch does not.



## Call an allowlisted read

Read calls are **default-deny**: only the reviewed ADR-0010 allowlist runs,
by exact method name (TL method-name prefixes like `get*` are not treated as
proof of safety). Everything outside the allowlist is blocked with exit 2
before configuration loading or session acquisition.

```bash
tg --json api users.getFullUser --params '{"id": "@alice"}'
```

| Flag | Effect |
| --- | --- |
| `--params JSON` | required; a JSON object mapped to the method's TL fields |
| `--write` | authorize a mutating call (see below) |
| `--confirm METHOD` | required in addition to `--write` for destructive verbs |

## Pass parameters and read the result

`--params` is a JSON object. Peer-typed fields accept `@username` or a
numeric id string and are resolved to an `InputPeer` through the session's
entity cache; a nested object with a `"_"` key selects a TL constructor by
name (`Input*` plus the `channels.getParticipants` filters and
`ChatAdminRights` / `ChatBannedRights` for admin/ban writes); binary fields
are base64. The response is:

```json
{"method": "users.getFullUser", "result": {"...": "..."}}
```

`result` is the TL object as a dict (or a bare JSON scalar — `true`, a
number, `null` — when the RPC itself returns a bare Bool/int/null, e.g.
`account.updateStatus`). Its shape mirrors the pinned Telethon TL layer and
is exempt from the usual JSON stability rules: it can change when that pin
is upgraded, unlike every other field tgcli emits.

## Authorize a write

Anything outside the read allowlist is a mutation and requires `--write`.
The same gates as every other mutation apply first: `--readonly`,
`TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` all block it with exit 2 before
configuration, session, or network work (see [safety](safety.md)). `auth.*`
and `account.*` are excluded wholesale, so this escape hatch cannot touch
credentials or account lifecycle even with `--write` — see below.

```bash
tg --json api messages.sendMessage \
  --params '{"peer": "@alice", "message": "hi"}' --write
```

## Confirm a destructive verb

Destructive verbs — `delete*`, `reset*`, `leave*`, `block*`, `edit*Admin*`,
`edit*Banned*` — additionally require an exact typed `--confirm METHOD`
matching the method name:

```bash
tg --json api channels.deleteChannel \
  --params '{"channel": "@mychannel"}' --write \
  --confirm channels.deleteChannel
```

A missing or mismatched `--confirm` value blocks the call with exit 2.

## Permanent denylist

`auth.*` and `account.*` are excluded wholesale from `tg api --write`
(ADR-0092), mirroring the read path's own ADR-0010 exclusion of the same two
namespaces: credential and account-lifecycle surface stays with
`tg accounts`, never with an arbitrary raw call. `--write` does not lift
this — session lifecycle and account mutation are simply not reachable
through `tg api`, whatever the method name.

Four methods carry this denial by name too, even though the namespace rule
above already covers them, in case that wholesale rule is ever narrowed for
one of the two namespaces:

| Method | Reason |
| --- | --- |
| `account.deleteAccount` | account deletion |
| `auth.logOut` | session lifecycle |
| `auth.resetAuthorizations` | session lifecycle |
| `account.resetAuthorization` | session lifecycle |

## Auditing

Every authorized raw write appends one JSON line to `audit.jsonl` before
dispatch, same as any other mutation — see [safety](safety.md#audit-log).

## Pulling story media: the `access_hash` boundary

`upload.getFile` is allowlisted so story-media bytes are reachable through
the raw surface. The result sanitizer strips `access_hash` and `secret` from
every printed result, so a follow-up `upload.getFile` call cannot be built
from a previous `tg api` output: video-story documents need
`InputDocumentFileLocation(id, access_hash, file_reference, thumb_size)`, and
the `access_hash` is not present anywhere in allowlisted read output. Photo
stories can still be pulled via `InputPeerPhotoFileLocation` (peer + photo id,
no `access_hash`). For document media, use
[`tg media download`](media.md) on the story link, or a Telethon script on
the tgcli venv — `tg api` is a read surface, not a download client. Note also
that `upload.File.bytes` prints as a `b'...'` string (JSON has no bytes
type), so raw pulls are not a byte-exact pipeline.

## See also

- [safety](safety.md) — the preview/gate/audit model that `--write` shares
  with every other mutation
- [../CONTRACT.md](../CONTRACT.md) — §6, the full passthrough contract
- [../../SKILL.md](../../SKILL.md) — "`tg api` — last resort"
- [../decisions/ADR-0008-raw-api-passthrough.md](../decisions/ADR-0008-raw-api-passthrough.md)
- [../decisions/ADR-0010-raw-api-read-allowlist.md](../decisions/ADR-0010-raw-api-read-allowlist.md)
- [../decisions/ADR-0092-api-write-auth-account-namespace-deny.md](../decisions/ADR-0092-api-write-auth-account-namespace-deny.md)
- [../FEATURES.md](../FEATURES.md) — per-namespace coverage: what has a wrapped
  command vs. `tg api` only
