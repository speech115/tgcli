# Run persistent foreground jobs

`tg jobs` persists typed work between foreground invocations. It is not a
daemon and does not install a scheduler. Telegram/archive/clone work and local
transcription use independent lanes; launchd or another caller decides when
to wake them.

Add one stable key:

```bash
tg --json jobs add archive-transcribe \
  --key archive-transcribe-hourly --max-attempts 3 --priority normal
tg --json jobs add archive-sync --key archive-sync-hourly \
  --max-events 500 --max-dialogs 20 --max-media 50
tg --json jobs add archive-backfill --key private-backfill \
  --private --limit 100
tg --json jobs add clone-sync --key source-clone -100123
```

The exact same queued spec is a no-op. To change its spec or priority, pass
`--replace`; a running generation must be cancelled and reach a checkpoint
first. Once a generation is terminal, adding the same spec creates the next
generation.

Inspect the latest generations and one key's newest 200 events:

```bash
tg --json jobs list
tg --json jobs show archive-transcribe-hourly
```

Run the local lane with an explicit wall-clock bound:

```bash
tg jobs run --lane local --max-runtime 300 --json
```

Each quantum asks the existing archive transcription queue for one item. The
runner keeps selecting eligible work until the cap or an idle frontier. A
single Parakeet invocation may overrun the cap; it is never killed midway.
The lane opens no Telegram session and rejects `--session-role`.

Authorize a separate role once, then run the Telegram lane:

```bash
tg accounts login main --role job --json
tg jobs run --lane telegram --session-role job --max-runtime 300 --json
```

The Telegram lane never falls back to the primary session. Its quanta are one
incomplete backfill dialog, one full changes difference plus bounded catch-up
and media work, or one 50-batch clone window. The first run binds the registry
to the live Telegram user; a later user mismatch refuses before selecting
work. Rate limits stay queued until their reported retry time.

Cancel safely:

```bash
tg --json jobs cancel archive-transcribe-hourly
```

Queued work cancels immediately. Running work records the request and stops at
the next durable boundary. A process crash releases the lane `flock`; the next
run returns stale running work to its existing archive checkpoint.

The registry is `~/.local/state/tgcli/jobs/<alias>/jobs.db` (SQLite/WAL).
`tg store stats` includes it; `store cleanup` never removes it. `--readonly`
and `TGCLI_READONLY=1` block add/cancel/run. `TGCLI_NO_SEND=1` does not block
the local lane because it performs no Telegram operation; it always blocks the
Telegram lane.
