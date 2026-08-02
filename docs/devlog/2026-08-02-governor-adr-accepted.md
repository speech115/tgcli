## 2026-08-02 — ADR-0072 accepted; governor map closed out (Claude)

**Did:** flipped ADR-0072 from `proposed` to `accepted` after #140's canary,
folding the evidence into the ADR itself; rewrote decision 3 to state its
measurement basis; recorded the supersession bookkeeping for ADR-0045 and
ADR-0052; reconciled a four-release version drift; closed #120 with a
resolution pointer and opened the two follow-ups the map deferred (#145
implementation slice, #146 job-model map). No production code changed.

**Decided — the CONTRACT, CHANGELOG, version bump and guide updates in #141's
scope move to the implementation slice, not this commit.** #141's own scope
line says `docs/CONTRACT.md` changes ship "same commit as the behaviour", and
the behaviour does not exist: ADR-0072 is accepted as a design and the governor
is unimplemented. Landing the contract text now — the exit-5 → exit-0 move, the
new journal fields, the `doctor` check — would have made the public CLI
contract describe a tool that isn't there, and a `2.0.0` CHANGELOG entry would
have announced a breaking change that hasn't happened. #145 carries all of it
together with the code.

**Decided — supersessions are declared but marked not-yet-effective.**
ADR-0072 supersedes ADR-0045 decision 1 and ADR-0052 decisions 1–5, but those
mechanisms are what actually runs today. Their index rows read "superseded by
ADR-0072, in force until the governor slice lands" rather than plain
`superseded`, and `docs/decisions/README.md`'s supersession notes say so
explicitly. Flipping them to unqualified superseded is a #145 task. An index
that declares the live mechanism dead while it is still the only thing
protecting the account would be worse than no note at all.

**Learned — the ADR's own cited precedent answered the question the ADR left
open.** The canary surfaced that decision 3 never said whether a governed
interval is measured start-to-start or end-to-start. Review of #144 pushed
further and it resolves cleanly:
`enforce_resolve_phone_cooldown()` writes its timestamp at
`src/tgcli/resolve_phone.py:54`, inside the pre-flight check before the RPC,
and the code's own comment calls it a reservation. The pattern decision 3
promises to generalize is unambiguously start-to-start. But decision 2's
`_call` seam must handle flood exceptions after the wrapped call anyway, so
stamping on return is the locally natural thing to write — an implementer
could follow decision 2 faithfully and land end-to-start without noticing the
contradiction. So the gap was never "underspecified"; it was "intent stated in
one decision, contradicted by the ergonomics of another, reconciled nowhere."
Decision 3 now says it outright, with the ~60% rate difference spelled out.

**Learned — the version drift was real and older than it looked.**
`pyproject.toml` and `docs/CONTRACT.md` both said `1.2.21` while
`src/tgcli/__init__.py` and the newest tag said `1.2.25` — four patch releases
of drift, through a green gate every time. Reconciled to `1.2.25` here so any
future bump and its CHANGELOG compare link build from the right base. Nothing
currently checks this, so it can silently recur; a gate check is the obvious
guard and is not in this change.

**Next:** #145 implements the governor and lands CONTRACT, CHANGELOG, version
and guides with the code. #146 charts the job model afterwards. Map #131 has
no open children left and can close.
