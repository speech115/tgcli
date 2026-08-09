## 2026-08-09 — api allowlist docs: ADR-0010 and CONTRACT §6 caught up

**Did:** The review of #158 found `upload.getFile` was added to the read
allowlist (41st method) without the ADR-0010 body update its own decision
clause mandates, and CONTRACT §6 still said 40. ADR-0010 now records the
2026-08-09 expansion (status line, decision paragraph, allowlist heading,
`upload (1)` entry with the review rationale: pure read, every location
constructor gated by access_hash/secret/peer+photo, sanitizer strips both).
CONTRACT §6 count corrected. A converter boundary test now builds
`GetFileRequest(InputDocumentFileLocation)` with a base64 `file_reference`
and the positional `thumb_size` — the exact story-media pull shape. The api
guide documents the `access_hash` boundary: video-story documents cannot be
re-pulled from sanitized `tg api` output (photo stories still can via
`InputPeerPhotoFileLocation`), and `upload.File.bytes` prints as a repr
string.

**Decided:** No sanitizer change — stripping `access_hash` is the correct
safety behavior; the limitation is documented instead.

**Learned:** ADR-0010's self-update clause is a manual step the docs gate
cannot catch (it checks counts of ADRs, not their body claims); a method
addition without the body update silently desynchronizes the safety record
from the code.

**Next:** 2.0.3 release rides this CONTRACT correction along with the
post-review campaign's ADR-0078/0079 slices.
