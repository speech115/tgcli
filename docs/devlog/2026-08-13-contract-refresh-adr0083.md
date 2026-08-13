## 2026-08-13 — CONTRACT refresh commit matches ADR-0083 (Composer)

**Did:** CONTRACT §11 refresh `--commit` documents
`begin_commit`/`finish_commit` retryable `.pending` (ADR-0083), not
`consume_preview`. Distinguishes preview-time FloodWait (new preview)
from commit-time FloodWait (same `--commit PREVIEW_ID`). README +
`check-docs` inverted from the obsolete “fresh preview” rule. Thermos T22.
Review fixes on tip of PR #246.

**Decided:** Full-lane (CONTRACT semantics + docs-gate enforcement). No
runtime code change.

**Learned:** ADR-0083 already moved clone commits to begin_commit; the
README/docs gate still enforced the pre-0083 spend-once story.

**Next:** Independent thermos re-check after fixes; owner-gated merge.
