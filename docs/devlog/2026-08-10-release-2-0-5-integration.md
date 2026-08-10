## 2026-08-10 — release 2.0.5 integration

**Did:** merged #181 (ADR-0084) into `main` and bumped 2.0.4 → 2.0.5 with the
CHANGELOG section and compare link. No ceilings needed truing up — the strict
architecture check passed untouched.

**Decided:** shipped as its own patch release rather than waiting to batch it
with later work. It closes a silent-corruption path in a released command, and
`media download`'s resume record changes shape, so operators should be able to
name the version where that happened.

**Learned:** merging into the default branch made `Closes #180` fire on its
own — the contrast with 2.0.4, where #178 went into a feature branch and left
#169/#170 open after the release, is the whole lesson about stacked merges in
one line.

**Next:** nothing open from the #169–#175 wave or its review follow-ups. #179
stays closed not-planned.
