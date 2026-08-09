# ADR-0010: Explicit phase-2 raw API read allowlist

Status: accepted (2026-07-10); allowlist expanded to 35 methods after the
2026-07-10 batch review, then to 36 methods on 2026-07-21 when ADR-0029
reconsidered `contacts.resolvePhone`, then to 40 methods on 2026-07-22 when
four read-only `stories.*` methods were added for story-viewer analytics,
then to 41 methods on 2026-08-09 when `upload.getFile` was added for
story-media pulls (issue #156 minimal fallback)

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
A batch review on 2026-07-10 expanded it to the 35 methods below. On
2026-07-21, ADR-0029 reconsidered `contacts.resolvePhone` for the identity
layer (`tg resolve` on a `+phone` ref) and moved it into the allowlist,
bringing the total to 36; see that method's note under `contacts` below.
On 2026-07-22 four read-only `stories.*` methods were added (story-viewer
analytics via `tg api`), bringing the total to 40. On 2026-08-09
`upload.getFile` was added (issue #156: the minimal fallback for pulling
story media that the wrapper `media download` uses through Telethon's
`iter_download`), bringing the total to 41; see its note under `upload`
below. Every method not listed exits 2 before configuration loading, session
acquisition, or network dispatch.

New raw API methods require an ADR-0010 update and a regression test proving
the exact method reaches the dispatcher. Method names and namespaces are never
used as evidence that an operation is read-only.

### Allowlist (41 methods; `contacts.resolvePhone` 2026-07-21; `stories.*` 2026-07-22; `upload.getFile` 2026-08-09)

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

contacts (4): `contacts.resolveUsername`, `contacts.search`,
`contacts.getContacts`, `contacts.resolvePhone` (reconsidered and accepted
under ADR-0029 for the identity layer's `tg resolve` on a `+phone` ref;
`resolvePhone` only, never `contacts.importContacts` — the caller supplies
one already-known phone number, and Telegram returns not-found when the
target's privacy settings disallow the lookup, so the call cannot be used to
enumerate numbers. A shared ~3s client-side cooldown applies to both
`tg resolve +…` and raw `tg api contacts.resolvePhone`, exiting 5 when
hit.)

photos (1): `photos.getUserPhotos`

stats (4): `stats.getBroadcastStats`, `stats.getMegagroupStats`,
`stats.getMessageStats`, `stats.getMessagePublicForwards`

stories (4): `stories.getPeerStories`, `stories.getStoriesArchive`,
`stories.getStoriesByID`, `stories.getStoryViewsList` (read-only story and
viewer analytics; no story publish/delete/pin methods)

upload (1): `upload.getFile` (pure read: `location`, `offset`, `limit`,
`precise`, `cdn_supported` — no counters, no mutation flags). Every
`TypeInputFileLocation` constructor the converter accepts is gated by a
server-validated secret: `InputDocumentFileLocation`/`InputPhotoFileLocation`/
CDN variants need the `access_hash`, raw `InputFileLocation` needs the
uploader-only 128-bit `secret`, and `InputPeerPhotoFileLocation` needs the
peer plus photo id. The result sanitizer strips `access_hash` and `secret`
from all output, so document bytes fetched via `tg api` cannot be re-pulled
from the printed JSON; `media download` uses the unsanitized internal path.

### Reviewed and rejected (2026-07-10)

| Method | Reason |
|--------|--------|
| `messages.getMessagesViews` | the `increment` flag mutates view counters |
| `messages.getBotCallbackAnswer`, `messages.getInlineBotResults` | bot interaction; a third party observes the call |
| `contacts.getLocated` | publishes live geolocation |
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
