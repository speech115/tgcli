# tgcli guide

Task-shaped pages for using `tg`: one per command area, each answering "how do
I do X" with real invocations and the JSON you get back.

Three documents describe this CLI, and they do not overlap:

| Document | Audience | Job |
| --- | --- | --- |
| **this guide** | humans | how to perform a task, with worked examples |
| [SKILL.md](../../SKILL.md) | agents | one-line routing table, loaded into context |
| [CONTRACT.md](../CONTRACT.md) | both | versioned law: flags, JSON shapes, exit codes |

Where a guide page and the contract disagree, **the contract is right and the
page is a bug** ([ADR-0041](../decisions/ADR-0041-user-facing-guide-split.md)).

## Start

| Page | Covers |
| --- | --- |
| [overview](overview.md) | the execution model, streams, exit codes, where state lives |
| [install](install.md) | `uv sync`, PATH linking, `config.toml`, first health check |
| [quickstart](quickstart.md) | a first session end to end, in seven commands |
| [accounts](accounts.md) | `accounts list\|import`, alias selection, session locks |

## Reading

| Page | Covers |
| --- | --- |
| [dialogs](dialogs.md) | `dialogs`, `info`, `count` |
| [read](read.md) | `read`, `latest`, `message`, `thread`, and the pagination recipes |
| [search](search.md) | per-dialog and global `search` with filters |
| [contacts](contacts.md) | `contacts`, `resolve`, `mutual-chats` |
| [batch](batch.md) | `batch` — read-only operations from JSONL on stdin |

## Writing

Every page in this group is preview → commit unless it says otherwise.

| Page | Covers |
| --- | --- |
| [send](send.md) | `send`, the two-step flow, and the retry rule |
| [editing](editing.md) | `edit`, `delete` |
| [forward](forward.md) | `forward` |
| [drafts](drafts.md) | `draft show\|list\|set\|clear` |
| [formatting](formatting.md) | `--format plain\|md\|html` and custom emoji |
| [inbox](inbox.md) | `mark-read`, `mark-unread`, `dialog pin\|archive\|mute` |

## Data

| Page | Covers |
| --- | --- |
| [media](media.md) | `media manifest`, `media download` |
| [export](export.md) | `export messages`, `export subscribers` |
| [clone](clone.md) | `clone status\|init\|sync\|export-state` |

## Operations

| Page | Covers |
| --- | --- |
| [doctor](doctor.md) | offline-first health checks, `--connect` |
| [store](store.md) | `store stats\|cleanup` — local state hygiene |
| [safety](safety.md) | readonly and no-send gates, preview TTL, the audit log |
| [api](api.md) | `tg api` — the raw TL escape hatch, and when not to use it |

## See also

- [FEATURES.md](../FEATURES.md) — which TL namespaces are wrapped, reachable
  via `tg api`, or deliberately excluded.
- [decisions/](../decisions/README.md) — why each behaviour is the way it is.
