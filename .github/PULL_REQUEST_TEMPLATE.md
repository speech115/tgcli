<!--
One coherent slice per PR. Unrelated cleanup, tooling, and product behavior
belong in separate PRs. Full rules: CONTRIBUTING.md and AGENTS.md.
-->

## What and why

<!-- The change in a few lines, and the problem it solves. -->

## Scope

- Kind: <!-- bug fix | docs | tooling | owner-requested behavior (ADR: ____) -->
- Authorized by: <!-- issue #N, ADR-NNNN, or "maintenance: bug fix" -->

## Evidence

<!--
Paste the real tail of ./scripts/gate.sh — never "tests pass".
A bug fix shows the reproducing test failing before the fix, passing after.
-->

```
$ ./scripts/gate.sh

```

## Documentation

- [ ] `docs/CONTRACT.md` updated (flags, JSON shapes, exit codes) — or unchanged
- [ ] Release shipped for a contract change: version bump + `CHANGELOG.md` section naming its ADR
- [ ] `docs/MAP.md` matches the tree (module added, moved, or removed)
- [ ] `README.md` / guide / `SKILL.md` reflect public commands, flags, and safety summaries
- [ ] `docs/ISSUES.md` / `docs/PROPOSALS.md` status updated when work graduated or shipped
- [ ] ADR added and indexed in `docs/decisions/README.md` (architectural decision)
- [ ] One `YYYY-MM-DD-slug.md` session entry added under `docs/devlog/`

## Safety

- [ ] stdout carries contract data only; diagnostics went to stderr
- [ ] Mutations stay behind preview → commit and the readonly / no-send gates
- [ ] State written through `tgcli.atomic.replace_text`, inside `~/.config/tgcli/` or `~/.local/state/tgcli/`
- [ ] No session material, credentials, phone numbers, or real message content in the diff or in this description
