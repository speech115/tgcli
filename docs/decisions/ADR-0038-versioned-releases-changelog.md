# ADR-0038: Tagged minor releases with a CHANGELOG entry per release

Date: 2026-07-23
Status: accepted

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
   exit codes. Since JSON changes are additive-only (AGENTS.md rule), a
   growing surface is a **minor** bump; a bug fix with no contract change
   is a **patch**; a contract break would be **major** and needs its own
   ADR.
3. **One feature, one release.** A merged feature branch that changes the
   contract bumps the version and lands its CHANGELOG section in the same
   commit as the bump, then gets a `vX.Y.Z` tag. Versions are not allowed
   to accumulate unreleased work again.
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
