# ADR-0020: Clone initializes the destination channel profile

Status: accepted (2026-07-16).

Amends ADR-0017 after live acceptance showed that a destination with copied
posts but no source avatar or description did not look like a complete clone.

## Context

`tg clone init --commit` created a private destination and copied its title,
but left the destination description and avatar empty. Both values belong to
the channel identity and should be present before message synchronization
starts.

## Decision

- After destination creation or recovery and title normalization, init reads
  the full source channel profile.
- A non-empty source description is copied with an audited
  `messages.editChatAbout` request. An empty description causes no mutation.
- A non-empty source photo is downloaded into a temporary directory, uploaded,
  and installed with an audited `channels.editPhoto` request. The temporary
  file is removed on success or failure. A missing photo causes no mutation.
- Profile reads, downloads, uploads, and edits use the clone cooldown wrapper;
  Telegram FloodWait deadlines therefore remain persisted in clone state.
- A profile failure leaves the recorded destination intact and returns a policy
  error. A new preview can safely retry init without creating another channel.
- Profile behavior lives in `clone/profile.py`, capped at 75 lines. Existing
  `commands/clone.py` and `clone/state.py` budgets remain unchanged.

## Consequences

An init result with `status: ready` now means the available title, description,
and avatar were applied before the command returned. Repeated init may reapply
the same metadata, but never creates a second destination once its id is saved.

Telethon's profile-photo download exposes a still image. Static avatars retain
their visual content after Telegram re-encoding; animated or video avatar motion
is not preserved by this implementation.
