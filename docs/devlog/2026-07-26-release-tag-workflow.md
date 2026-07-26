## 2026-07-26 — Make ADR-0038's release bookkeeping self-enforcing (Claude Fable 5)

**Did:** merged the 1.2.16 campaign (PR #85, squash `efbb9a9`), then chased the
tag question the release surfaced. Counting across the seven releases
`1.2.10`–`1.2.16`: version bump and `CHANGELOG` section 7/7, compare link 2/7,
tag 0/7. The link half was new information — `1.2.12`–`1.2.16` had sections
with **no** `[x.y.z]:` definition at all, so their headings rendered as literal
brackets on GitHub, and the two links that did exist pointed at tags that were
never created. Fixed both halves at the source rather than by hand: the docs
gate now checks that every release section has its link definition, that the
link names the version it defines, and that no definition outlives its section
(`scripts/check-docs.py`, six tests in `tests/test_check_docs.py`); a new
`Release tag` workflow tags the merged commit on push to `main`. Backfilled
the five missing links. Gate green.

**Decided:** no ADR, and **no change to ADR-0038's rule** — releases are still
tagged `vX.Y.Z`; only the executor moved from "the merging session" to CI.
Same reasoning Codex applied to the CI-trigger fix: this is repository
integration mechanics, not the CLI, safety model, dependency graph, or public
contract. Rejected the alternative of amending ADR-0038 to make `CHANGELOG.md`
the single source of truth: the `CHANGELOG` is itself what drifted, so
promoting it would have entrenched the failure and cost a rewrite of 17
existing compare links. The seven missing tags stay owner work — pushing
`refs/tags/*` is still 403 for sessions, which is exactly why the workflow
exists.

**Learned:** the independent review earned the PR outright. My first workflow
tagged `HEAD` whenever `refs/tags/v$version` was absent — which, with
`v1.2.10`–`v1.2.16` all still untagged, would have pinned `v1.2.16` to this
PR's own merge commit instead of `efbb9a9` on the very first run. The automation
built to fix the tag gap would have been its worst instance. The guard is to
compare `__version__` against `github.event.before` and tag only when *this
push* moved it, refusing to guess whenever the previous tip is unreadable.
Reviewing also found the checker's set logic collapsed duplicate sections and
let a duplicate `[x.y.z]:` definition silently overwrite the correct one —
precisely the shape a botched conflict resolution leaves in a shared,
integrator-merged file.

The rule failed 7/7 not because anyone ignored it but because its
last step was scheduled *after* the merge, when the session that owed it was
already over. Every invariant in this repo that actually holds is enforced by
`gate.sh`, which runs before every commit; anything asking for a manual
follow-up is a wish, not a rule. Worth applying that test to the rest of
AGENTS.md. Also: the gate's own blind spot was narrow and obvious in
hindsight — `check-docs.py` validated links inside `docs/guide/` and nothing
else, so `CHANGELOG.md` was never read by any check.

**Next:** land this PR on main; confirm the Release-tag workflow can push
`refs/tags/*` on the next version bump (tags `v1.2.10`–`v1.2.16` already
backfilled locally).