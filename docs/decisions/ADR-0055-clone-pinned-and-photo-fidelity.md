# ADR-0055: Carrying the pinned message, and diagnosing photo downscaling

Date: 2026-07-25
Status: accepted

## Context

The 2026-07-25 fidelity audit of the finished `[икона]` clone compared both
legs against their sources and found two gaps that survive an otherwise
faithful copy.

**The pinned message is not carried.** The source channel pins post 12 —
by `id_map` that is destination post 9 — and the destination pins nothing
(`pinned_msg_id: None`). A pinned post is the first thing a reader sees, and
on a course channel it is usually the table of contents, so its absence is
the most visible single difference between the two channels. `clone init`
already copies the title (ADR-0044), the avatar, and the about text
(`commands/clone.py:287-360`); pinning was simply never in that list. There
is no `pin` surface anywhere in the tool: `tg dialog pin` toggles a *dialog*
in the user's own list (`commands/dialog.py:22-31`), and
`messages.UpdatePinnedMessage` appears nowhere in `src/tgcli`.

The information is already in hand. `_copy_profile` calls
`GetFullChannelRequest` at init (`commands/clone.py:304`) and
`types.ChannelFull` carries `pinned_msg_id`; the field is never read.

But init is the wrong moment. At init the destination is empty — the post
the source pins does not exist yet and has no `id_map` entry. The pin can
only be placed once its target has been copied.

**Five photos came back smaller.** Source 15 is 1024×1024 / 45 216 bytes;
its clone is 800×800 / 41 902 bytes, and sources 33, 35, 58, 81 show the
same shape. Twelve of eighteen documents and six of eleven photos are byte
identical, so this is specific to photos, not to the transfer path.

The cause is *not* established, and one plausible suspect is already ruled
out. The clone has two download paths (`commands/clone.py:629-655`): files
over `CHUNK_SIZE` (512 KB) go striped, everything else goes through
`download_media`. Telethon's `download_media` sorts `photo.sizes` and takes
the largest; `download_striped` reaches `utils._get_file_info`, which takes
`sizes[-1]` as-is, trusting an order nothing guarantees. That is a real
latent defect — but all five affected photos are far under 512 KB, so they
never touched the striped path. Something else downscaled them, most likely
Telegram's own re-encoding of an uploaded `InputMediaUploadedPhoto`
(`commands/clone.py:619-620` passes `file` and nothing else). No ADR or test
in this repository has ever examined photo quality.

## Decision

1. **`clone sync` places the pin when the posts leg is exhausted.** On a run
   that ends with `more: false`, if the source's `pinned_msg_id` maps to a
   destination id through `id_map`, the clone pins it with
   `messages.UpdatePinnedMessage`. Tying it to `more: false` means two extra
   RPCs on the runs that complete a catch-up, not on every batch of a long
   one.

2. **Pinning is silent and additive.** The request sets `silent=true` — a
   clone that gains subscribers must not notify them about a pin that
   re-enacts history. If the destination already pins *anything*, the clone
   leaves it alone: the owner's own pin outranks a mirrored one.

3. **Unpinning is never mirrored.** If the source later unpins, the clone
   keeps its pin. Mirroring a removal would let a source-side change delete
   destination state the owner may have chosen deliberately, and the audit
   trail for "why did my pin vanish" is far worse than a stale pin.

4. **An unmappable pin is reported, not guessed.** A `pinned_msg_id` absent
   from `id_map` (service message, skipped-unsupported post, deleted source)
   pins nothing and says so in the sync JSON — a new `pinned` object with
   the source id, destination id when known, and a status of `set`,
   `unchanged`, `unmapped`, or `occupied`. This is a CONTRACT §11 change and
   ships as a tagged release (ADR-0038).

5. **The photo work starts as a measurement, not a fix.** Before any code
   changes, compare bytes at each hop for one affected photo: the source's
   largest `PhotoSize`, the file on disk after download, and what the
   destination reports after upload. Only that tells us whether the tool
   picked a small size or Telegram re-encoded the upload.

6. **The striped size selection is corrected regardless of what the
   measurement shows.** Picking the largest `PhotoSize` explicitly instead of
   trusting `sizes[-1]` is right on its own terms; it just is not the
   explanation for these five photos and must not be presented as their fix.

7. **If the loss proves to be Telegram's re-encode, it is documented, not
   worked around.** The honest outcome is a CONTRACT sentence saying a
   reuploaded photo may come back smaller than the source. Sending photos as
   documents to dodge re-encoding is rejected: it would change how every
   image renders in the clone — no inline preview — to protect a few hundred
   pixels.

## Consequences

- The clone's channel header stops being empty, which is the single most
  visible fidelity gap the audit found.
- Decisions 2 and 3 make the pin a one-way, non-destructive mirror: the
  clone can gain a pin from the source but never lose one to it. A source
  that re-pins a different post leaves the clone showing the old pin — a
  known, accepted divergence, and the reason the sync JSON reports status
  instead of staying silent.
- Two extra RPCs per completing run (`GetFullChannelRequest` on the source,
  and the pin itself when needed). Runs that stop early — the normal outcome
  on a protected source — pay nothing.
- The photo item may end in documentation rather than code. That is an
  acceptable result and is stated up front so the implementing agent does
  not feel obliged to invent a fix; what is *not* acceptable is a fix that
  claims to address the audit's five photos without evidence that it does.
- The striped-path correction removes a latent defect that would eventually
  have bitten a photo over 512 KB, where the size list order is the only
  thing standing between the clone and a thumbnail.
