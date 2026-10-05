# Contributing to tgcli

Bug reports and fixes are welcome. For new behavior, open an issue first: the
maintainer decides what lands.

```bash
git clone https://github.com/speech115/tgcli.git
cd tgcli
uv sync
uv run tg --help
```

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). The test suite
needs no Telegram account.

Before opening a pull request, run `./scripts/gate.sh`; CI runs the same
steps. The working rules are in
[AGENTS.md](AGENTS.md). Never commit session files or secrets — see
[SECURITY.md](SECURITY.md).
