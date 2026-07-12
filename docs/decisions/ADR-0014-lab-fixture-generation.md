# ADR-0014: Generate R1 media fixtures with local command-line codecs

Status: accepted (2026-07-12).

## Context

The first R1 controlled-lab run uploaded deterministic random bytes with media
filenames and Telegram document attributes. Telegram correctly treated the
invalid MP3, MP4, OGG, and WebP payloads as generic documents. That made the
per-kind fidelity verdict inconclusive.

Checking binary fixture assets into the repository would violate the R1 plan's
no-media-bytes rule and make provenance harder to review. Implementing six
container encoders in Python would add more untrusted code than the lab needs.

## Decision

The explicitly invoked R1 research harness may use local `ffmpeg` and `cwebp`
executables to generate valid minimal fixtures in a per-run temporary directory.

- This is a lab-only tool dependency. It is not a tgcli runtime dependency and
  is not added to `pyproject.toml`.
- Tool and encoder availability is checked before configuration loading,
  Telegram session acquisition, or any mutation.
- Commands use argument arrays, never a shell, and capture errors without
  leaking message or account data.
- Generated files are named, have explicit MIME types, and are deleted with the
  temporary directory after seeding.
- The fixture smoke test validates container/codec metadata and deterministic
  bytes on the active toolchain. Telegram classification still requires the
  controlled live acceptance run.
- No generated media is committed to the repository.

## Consequences

The controlled lab now tests actual media containers instead of filename
claims. Reproducibility is scoped to the installed codec toolchain; a missing
binary or encoder blocks the seed phase before network access with exit 4.
Production mirror code remains independent of these tools.
