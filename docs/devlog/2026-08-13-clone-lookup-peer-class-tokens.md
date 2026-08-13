## 2026-08-13 — Clone lookup uses peer-class tokens (Cursor)
**Did:** implemented thermos T18 on T05 tip `7391380`.
`clone.lookup.matches` now derives one canonical numeric token from the
recorded source kind through Telethon's `PeerUser`, `PeerChat`, or
`PeerChannel` encoding. Added public CLI coverage for all five clone source
kinds: broadcast, megagroup, forum, basic, and dialog. Each kind accepts its
own token and refuses tokens from the other peer classes. Updated the shared
`export-state` matcher expectation, CONTRACT §11, MAP, and the ADR index.
The clean RED run was 6 failed / 4 passed; after the minimal lookup change,
the new cases were 10 passed. Focused status, export, docs, lint, and format
verification was green: 82 passed.
**Decided:** ADR-0101 records the numeric filter semantics because removing
the bare channel-id alias changes CONTRACT behavior. It depends on ADR-0100's
user/chat/channel distinction and adds no second kind model.
**Learned:** the status test helper had been mutating `source_kind` after
construction, which left forum state internally inconsistent; passing the
kind to `CloneState.new` makes the fixture represent persisted production
state.
**Next:** merge T05 first, then land this stacked T18 commit and perform the
integrator-owned patch release bookkeeping.
