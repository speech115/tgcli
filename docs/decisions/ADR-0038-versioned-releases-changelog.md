# ADR-0038: Tagged releases with a CHANGELOG entry per release

Date: 2026-07-23
Status: accepted (rule 3 mechanics amended by ADR-0058: the integrator
assigns the version and CHANGELOG section at merge; feature branches never
touch them); rule 3 superseded by ADR-0120 (release on owner request)

## Context

`pyproject.toml` and `src/tgcli/__init__.py` still said `1.0.0` while 93
commits and ten ADRs (0028…0037) had shipped on top of the v1.0.0 tag: the
whole agent-correspondence surface, discovery and inbox commands, data
plumbing, and two clone changes. `tg --version` therefore lied to every
consumer, and there was no single document that answered "what changed
between the tag I have and the tag I want".

The existing docs do not cover this. `docs/DEVLOG.md` is session-scoped and
newest-first — it records *how work happened*, not what a release contains.
The ADR index records *why* decisions were made, one row per decision, with
no notion of release boundaries. Both are agent-facing; neither is a
consumer-facing release note.

The failure mode is drift by accumulation: nobody bumps, so the next bump
has to be reconstructed from git archaeology — exactly the work this
session had to do.

## Decision

1. **`CHANGELOG.md` at the repo root** is the consumer-facing release
   record. Keep a Changelog format, newest release on top, one section per
   released version, each bullet naming the ADR that governs it. It is
   short by design: a reader who wants detail follows the ADR link.
2. **Semver is measured over `docs/CONTRACT.md`** — flags, JSON shapes,
   exit codes — but the **minor** digit is a milestone the owner declares,
   not an automatic consequence of shipping. Concretely: a feature or a fix
   ships as a **patch**, because the surface only ever grows additively
   (AGENTS.md rule) and additive growth breaks nobody; the owner raises the
   minor when the accumulated set is worth announcing as one; a contract
   break is **major** and needs its own ADR.
3. **One feature, one release.** A merged feature branch bumps the patch
   version and lands its CHANGELOG section in the same commit as the bump,
   then gets a `vX.Y.Z` tag. Versions are not allowed to accumulate
   unreleased work again.
4. **Version lives in two files** — `pyproject.toml` and
   `src/tgcli/__init__.py` (which `tg --version` prints). Both move
   together.

## Consequences

- `tg --version` is trustworthy: it names a tag whose contents are written
  down.
- Release notes cost is paid per feature, while the author still remembers
  the change, instead of being reconstructed from `git log` later.
- One more file to keep honest. It is cheap because it is short and because
  rule 3 attaches it to work that is already happening, but a feature
  merged without its CHANGELOG section is now an incomplete feature.
- 1.1.0 is the catch-up release covering ADR-0028…0037 in one section; it
  is the only section that will ever bundle several ADR waves.
- The minor digit carries owner intent instead of arithmetic: 1.2.0 will
  mean "a set worth announcing", not "some command was added". The price is
  that the digits alone no longer tell a reader that a new command exists —
  `CHANGELOG.md` is the only place that says so, which is what makes rule 1
  mandatory rather than nice-to-have.
- Rule 2 was written mechanically in the first draft of this ADR ("additive
  growth is a minor bump") and revised before this ADR was ever published,
  when the owner declared the first post-1.1.0 features patches. Recorded
  here because the mechanical version is the obvious one to drift back to.
