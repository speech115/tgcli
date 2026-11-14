# Operations

How the owner's Mac runs tgcli, how a merged change reaches it, and what to do
when something breaks. Every `tg` command and flag here is checked against the
real CLI by `scripts/check-docs.py`; the exit codes and JSON fields are
defined in [CONTRACT.md](CONTRACT.md), which wins where the two differ.

## Layout on the Mac

| Path | What it is |
|---|---|
| `~/Projects/tools/tgcli` | development checkout, where agents work |
| `~/Projects/tools/tgcli-prod` | git worktree of the same repository on branch `main`; this is what runs |
| `~/.local/bin/tg` | symlink to `tgcli-prod/.venv/bin/tg` (made by `scripts/install-link.sh`) |
| `~/.config/tgcli/` | config: accounts, `api_id` |
| `~/.local/state/tgcli/` | sessions, previews, audit, journal, clone and archive state |

Scheduled work (archive sync, transcription, clone sync) is launchd calling
the same `tg` on PATH; see the archive row in [SKILL.md](../SKILL.md) and
[Keeping clones current](#keeping-clones-current).

## Keeping clones current

One launchd job per clone runs `clone sync` on a short interval. tgcli keeps no
daemon: the job starts, finds out whether the source has anything new, and
exits.

```xml
<key>Label</key>
<string>com.tgcli.clone-sync.SOURCE</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/you/.local/bin/tg</string>
  <string>--account</string><string>A</string>
  <string>--session-role</string><string>job</string>
  <string>--max-runtime</string><string>240</string>
  <string>--json</string>
  <string>clone</string><string>sync</string><string>SOURCE</string>
</array>
<key>StartInterval</key>
<integer>120</integer>
```

The command is `tg --account A [--session-role job] --max-runtime 240 --json
clone sync SOURCE`. Leave out `--session-role job` if no one else uses the
account's primary session. With a role, authorize it once (`tg accounts login A
--role job --phone PHONE`), so a sync never holds the session your own
commands need.

Why it is safe and cheap to run this often:

- An idle run costs the session check plus one one-message history read of the
  source, and of its discussion group when comments are copied. It copies
  nothing, opens no destination and lists no roster, and says
  `"up_to_date":true`. A full run happens when something is new, and once a
  day for the roster (`participants`).
- A FloodWait that ends a sync (exit 5) is written into the clone as
  `retry_not_before`. Every run before that time exits 5 with the remaining
  `retry_after` and sends no request, so a short interval never sends into a
  live ban. `tg clone status` shows the field.
- `--max-runtime 240` ends a long copy as a normal stop (exit 0, `remaining`
  true); the next run resumes at the saved cursor. launchd starts no second
  instance while one runs, and the clone's own lock refuses an overlap with a
  manual run (exit 2).

## Shipping a merged change

`main` is what runs. A merge does nothing on the Mac until `tgcli-prod` moves.

### Before

- [ ] The PR's `checks` and `test` jobs are green, and so is the `main` run
      after the merge. A red `main` run means two green PRs conflict in
      meaning: fix forward before you move `tgcli-prod`.
- [ ] No one-shot long run is in flight: no `clone sync` or `archive sync`
      started by hand or by launchd is running right now
      (`pgrep -fl 'bin/tg '`). A run picks up the new code only on its next
      start, but swapping files under a running process is not worth it.
- [ ] Note the current commit, so a rollback is one command:
      `git -C ~/Projects/tools/tgcli-prod rev-parse --short HEAD`.

### Ship

```bash
cd ~/Projects/tools/tgcli-prod
git fetch origin main
git merge --ff-only origin/main
uv sync --frozen --extra fast   # frozen: never rewrite uv.lock; the extra keeps cryptg
```

The merge refuses anything but a fast-forward. If it refuses, someone
committed in `tgcli-prod` by hand: look at `git log origin/main..HEAD` before
doing anything else.

### Verify

- [ ] `tg --version` matches `pyproject.toml`.
- [ ] `tg --json doctor --connect` reports `"ok": true` for every account, and
      `runtime.python` points into `tgcli-prod/.venv` and `runtime.aes` is
      `cryptg`.
- [ ] One read: `tg --json dialogs --limit 3` exits 0.
- [ ] If the change touched clone or archive: the next scheduled run exits 0
      (the invocation journal shows its `exit_code`, see below).

### Roll back

Roll back when the verify step fails, or a scheduled run starts exiting 1
right after the ship:

```bash
cd ~/Projects/tools/tgcli-prod
git reset --keep <commit noted before>
uv sync --frozen --extra fast
```

Then open an issue or a fix PR. The next ship fast-forwards past the bad
commit once the fix is on `main`. Local state needs no rollback unless the
change migrated a SQLite schema: then the older code may refuse the newer
database, and the fix goes forward, not back.

## Release (only when the owner asks)

Releases are rare: one user runs `main` (ADR-0120). When asked:

- [ ] A PR bumps `version` in `pyproject.toml` and `__version__` in
      `src/tgcli/__init__.py` to the same `X.Y.Z`. A `Breaking:` PR title
      replaces a major bump (ADR-0120), so the number is the owner's call.
- [ ] After it merges, tag that merge commit: `git tag vX.Y.Z <sha>` and
      `git push origin vX.Y.Z`.
- [ ] `gh release create vX.Y.Z --generate-notes`, then trim the notes to what
      a user would notice.
- [ ] Ship to the Mac as above.

## When something breaks

Start with the exit code and the JSON error envelope on stdout
(`error.code` and `error.message`), then the journal:

```bash
tail -n 20 ~/.local/state/tgcli/invocations.jsonl
```

Each line has `command`, `exit_code`, `error` (the code only, such as
`CONFIG`), `error_site` (where in tgcli it failed) and, for exit 5, `retry_after`. It never holds
message text. Rerun the failing command with `-v` for Telethon debug logs on
stderr.

| Symptom | What it means | What to do |
|---|---|---|
| exit 3, `error.message` says `needs reauthentication` | the session is dead: revoked, expired, or the key is unregistered | `tg --json accounts login ALIAS --phone PHONE`, then `--continue LOGIN_ID --code …`; check with `tg --json accounts show ALIAS`. A role session (`--session-role job`) needs its own login with `--role job` |
| exit 3 after a wait (up to 120 s, less if `--max-runtime` is shorter), `error_site` `session:session_file_lock` | another tg run holds this session. `accounts login` and `accounts remove` do not wait: they exit 2 at once | wait for it, or find it with `pgrep -fl 'bin/tg '`. Scheduled jobs that overlap with manual work should use `--session-role job` |
| exit 3 from `archive sync`, `rebaseline` or `transcribe` | the same command is already running for this account (`sync` and `rebaseline` share one lock, `transcribe` has its own) | nothing; the next scheduled run continues |
| exit 5 with `retry_after` | Telegram FloodWait longer than 30 s, or any FloodWait in `clone init --commit`, `clone sync` or `tg run --write`. Nothing is stored: the wait is only in this error and in the journal row | wait `retry_after` seconds before the next run; an earlier rerun sends the request again and may draw a longer wait. A scheduler should skip its next start until then |
| exit 1 `TIMEOUT` with a `session … is busy; waiting` note on stderr | another tg run held the session for longer than `--timeout` (60 s by default) | wait for it, or pass a larger `--timeout`; the busy wait itself gives up after 120 s with exit 3 |
| `clone sync` or `archive sync` exits 1 mid-run | lost connection or an untranslated server error; the run stops rather than marking items failed | rerun the same command; both resume from their saved cursor |
| a run exits 0 with `stop_reason: "wall_clock_cap"` or `remaining: true` | it hit `--max-runtime` or a budget, not an error | nothing; the next run continues from the resume pointer |
| `archive status` lists `gaps` | Telegram could not deliver part of the update history (`differenceTooLong`, or `channelUnavailable` when a channel went private) | `tg --json archive rebaseline` restarts the cursor and clears the gap. It does not refetch what the gap lost: compare `tg --json archive read CHAT` with a live `tg --json read CHAT` if it matters |
| `clone init --commit` stops partway (a flood on the discussion link, say) | the preview is spent only on success | rerun the same `--commit PREVIEW_ID` while it is unexpired (5 minutes); it resumes |
| exit 2, `BLOCKED` | a safety gate refused (`--readonly`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`), or an argument was refused (a bad `--limit`), or a busy session on `accounts login`/`remove` | for the gates: intended in agent sessions. For an owner-approved write, prefix that one command with `TGCLI_READONLY=0`; `TGCLI_NO_SEND=1` must be unset too |
| state directory keeps growing | old previews, login attempts, abandoned clone media caches | `tg --json store stats`, then `tg --json store cleanup --older-than 7d` to see what would go, and add `--confirm` to delete. Sessions and their `.bak` are never deleted |
| `doctor` shows `runtime.aes: "pyaes"` | slow pure-Python crypto | `uv sync --frozen --extra fast` in `tgcli-prod` installs `cryptg` |

If none of these fits, keep the journal line and the stderr output, and open
an issue. Never open a `.session` file with bare `python3` to look inside: the
pinned Telethon writes a schema that older versions crash on.
