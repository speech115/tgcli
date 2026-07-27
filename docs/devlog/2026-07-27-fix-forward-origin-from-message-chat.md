## 2026-07-27 — Fix forward-origin label from message.forward chat (Cursor Grok)

**Did:** red test for issue #80 (MIAMIVICE `#50` shape: `ChannelPrivateError`
on `get_entity`, title available via `message.forward.get_chat()`);
`forwarded_author_of` now uses that helper when peer ids match and seeds
the author cache; ADR-0064 + CONTRACT clarification; live smoke on
`main` → `Переслано от Комьюнити Арсена Маркаряна`. Release bookkeeping
1.2.18.

**Decided:** ADR-0064 — accompanying-chat entity is still a truthful
`from_id` resolution, not a new attribution source.

**Learned:** Telegram can refuse GetChannels for a left/private origin
while still shipping that Channel in the same GetMessages `chats` vector
(`left=True`, title present).

**Next:** merge → tag; repair clone `#27` via `tg clone refresh` or edit;
close #80.
