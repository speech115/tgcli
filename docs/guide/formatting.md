# Formatting and custom emoji

`send`, `edit`, and `draft set` all take `--format {plain,md,html}`, choosing
how the message text (or file caption) is parsed into Telegram formatting
entities.

## Defaults per command

| Command | Default | Why |
| --- | --- | --- |
| `send` | `md` | preserves the historical Telethon client default |
| `edit` | `plain` | a surgical edit stays literal unless asked otherwise |
| `draft set` | `md` | mirrors `send` — a draft is a message-in-waiting |

## The three formats

```bash
tg --json send CHAT "TEXT" --format plain --preview
tg --json send CHAT "TEXT" --format md --preview
tg --json send CHAT "<b>TEXT</b>" --format html --preview
```

- `plain` — TEXT is sent verbatim with no entities; parsing is disabled, so
  literal `*`, `_`, `<` survive untouched.
- `md` — Telethon Markdown (the historical default for `send`).
- `html` — the full entity set below, including custom emoji and expandable
  quotes that Markdown cannot express.

The commit re-renders TEXT from the format stored in the preview and passes
explicit formatting entities — the preview only stores the raw markup and
chosen format string.

## HTML entity set

`--format html` supports:

| Entity | Markup |
| --- | --- |
| Bold | `<b>text</b>` |
| Italic | `<i>text</i>` |
| Underline | `<u>text</u>` |
| Strikethrough | `<s>text</s>` |
| Blockquote | `<blockquote>text</blockquote>` |
| Expandable blockquote | `<blockquote expandable>text</blockquote>` |
| Spoiler | `<tg-spoiler>text</tg-spoiler>` (or `<span class="tg-spoiler">text</span>`) |
| Inline code | `<code>text</code>` |
| Code block | `<pre>text</pre>` |
| Link | `<a href="URL">text</a>` |
| Custom emoji | `<tg-emoji emoji-id="ID">glyph</tg-emoji>` |

```bash
tg --json send CHAT "<b>bold</b> <tg-spoiler>hidden</tg-spoiler>" --format html --preview
tg --json edit CHAT ID "<b>bold</b> <blockquote expandable>quote</blockquote>" --format html --preview
```

## UTF-16-correct offsets

Every entity's `offset`/`length` — both in outgoing formatting entities and in
the `custom_emoji` field on read messages — is computed in UTF-16 code units,
matching Telegram's own entity encoding. Surrogate-pair emoji in the text
shift the offsets of following entities correctly instead of desynchronizing
them, so entities composed against `read`/`search` output line up when
reused in a `send`/`edit`.

## Custom-emoji workflow

Every read command (`read`, `search`, `message`, `export`) returns a
`custom_emoji` array on each message: a (possibly empty) list of
`{"id", "emoji", "offset", "length"}` objects. `id` is the reusable
`document_id`, always encoded as a **decimal string** — not a JSON number —
so IEEE-754 JSON parsers (including JavaScript's) cannot silently round it.
`emoji` is the fallback unicode glyph; `offset`/`length` are the UTF-16
positions described above.

Custom-emoji ids cannot be invented. To reuse one:

1. Harvest an id from a real post that already uses the emoji you want:

   ```bash
   tg --json message CHAT ID
   ```

   Read `custom_emoji[].id` from the response.

2. Drop it into an HTML send or edit:

   ```bash
   tg --json send CHAT '<tg-emoji emoji-id="5312016608254762367">😀</tg-emoji>' --format html --preview
   ```

Sending a message containing custom emoji requires the sending account to
have Telegram Premium; the glyph inside `<tg-emoji>` is only the fallback
rendering for clients that cannot show the custom asset.

## See also

- [send.md](send.md) — `--format` on send, preview/commit mechanics
- [editing.md](editing.md) — `--format` default divergence on edit
- [drafts.md](drafts.md) — `--format` on `draft set`
- [../decisions/ADR-0030-outgoing-formatting.md](../decisions/ADR-0030-outgoing-formatting.md) — why formatting and custom-emoji harvesting were added
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes
