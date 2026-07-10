# ADR-0010: Explicit phase-2 raw API read allowlist

Status: accepted (2026-07-10); allowlist expanded to 35 methods after the
2026-07-10 batch review

Supersedes: ADR-0008's phase-2 read-classification rule

## Context

ADR-0008 allowed raw API calls from method-name prefixes (`get*`, `search*`,
`check*`, and `resolve*`). Telegram TL method names do not prove an operation
is safe: `auth.checkPassword` checks a password and `account.getTmpPassword`
creates a credential. Both would pass the prefix rule before configuration,
session, and network safeguards could apply.

## Decision

Phase 2 uses a reviewed, explicit, default-deny allowlist. The initial
allowlist contained only `users.getFullUser`, required by Phase 2 acceptance.
A batch review on 2026-07-10 expanded it to the 35 methods below. Every
method not listed exits 2 before configuration loading, session acquisition,
or network dispatch.

New raw API methods require an ADR-0010 update and a regression test proving
the exact method reaches the dispatcher. Method names and namespaces are never
used as evidence that an operation is read-only.

### Allowlist (35 methods, reviewed 2026-07-10)

messages (18): `messages.getHistory`, `messages.getMessages`,
`messages.getReplies`, `messages.getDiscussionMessage`, `messages.search`,
`messages.searchGlobal`, `messages.getSearchCounters`, `messages.getDialogs`,
`messages.getPeerDialogs`, `messages.getSavedDialogs`,
`messages.getSavedHistory`, `messages.getCommonChats`,
`messages.getUnreadMentions`, `messages.getUnreadReactions`,
`messages.getMessagesReactions`, `messages.getMessageReactionsList`,
`messages.getFullChat`, `messages.getForumTopics`

channels (7): `channels.getFullChannel`, `channels.getChannels`,
`channels.getMessages`, `channels.getParticipant`,
`channels.getParticipants`, `channels.getAdminLog`,
`channels.getAdminedPublicChannels`

users (2): `users.getUsers`, `users.getFullUser`

contacts (3): `contacts.resolveUsername`, `contacts.search`,
`contacts.getContacts`

photos (1): `photos.getUserPhotos`

stats (4): `stats.getBroadcastStats`, `stats.getMegagroupStats`,
`stats.getMessageStats`, `stats.getMessagePublicForwards`

### Reviewed and rejected (2026-07-10)

| Method | Reason |
|--------|--------|
| `messages.getMessagesViews` | the `increment` flag mutates view counters |
| `messages.getBotCallbackAnswer`, `messages.getInlineBotResults` | bot interaction; a third party observes the call |
| `contacts.getLocated` | publishes live geolocation |
| `contacts.resolvePhone` | enables phone number enumeration |
| `messages.getExportedChatInvite`, `messages.getExportedChatInvites`, `messages.getChatInviteImporters`, `messages.getAdminsWithInvites` | secret invite links appear in output |
| `messages.getSponsoredMessages` | ad impression side effects |
| `auth.*`, `account.*` | excluded wholesale: credential and account-lifecycle surface |

## Consequences

- `auth.checkPassword` and `account.getTmpPassword` are blocked despite their
  read-looking names.
- Phase 2 exposes a reviewed raw read surface; additional use cases are still
  reviewed one method (or one explicit batch) at a time, default-deny.
- Every allowlisted method carries a regression test that it resolves to a
  real TLRequest of the pinned Telethon layer; every rejected method above
  carries a regression test that it exits 2 before config loading.
- ADR-0008 continues to govern the passthrough rationale and the future
  phase-4 write-path requirements.
