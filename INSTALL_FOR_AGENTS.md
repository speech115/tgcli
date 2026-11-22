# Install tgcli: a guide for AI agents

You are an AI agent. A person asked you to install tgcli, the `tg` command for
Telegram, and connect their Telegram account. Follow the steps below in order
and run every command yourself. Ask the person nothing. Their only task is the
login page in step 5, plus approving your commands if the app asks. Every step is safe to run again, so after an error, fix
the cause and repeat the step.

## Rules for the whole run

- Never tell the person to open a terminal or type a command.
- Talk to the person in their language, in plain words, with no commands or
  paths. Most tgcli users speak Russian.
- Each shell call starts fresh. Keep the `export PATH` line at the top of every
  block.
- Codex: these steps write outside the project folder and need the network.
  Run each command outside the sandbox (escalated permissions) and let the
  person approve it.
- Antigravity: its terminal sandbox has no network. Ask to run `uv`, `curl`
  and `tg` outside the sandbox.
- tgcli runs on macOS and Linux. The commands assume a POSIX shell (zsh or
  bash).

## 1. Check the system

```bash
uname -sm
```

- If it prints `Darwin arm64`, `Darwin x86_64`, `Linux x86_64` or
  `Linux aarch64`, go on.
- If it prints anything else, Windows included, stop. Tell the person that
  tgcli runs on macOS and Linux only.

## 2. Install uv

[uv](https://docs.astral.sh/uv/) installs tgcli and the Python it needs. Its
official installer puts `uv` in `~/.local/bin`.

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

## 3. Remove the old `tg`

The archived project [speech115/tg](https://github.com/speech115/tg) (Python
package `tg-harness`) also installs a command named `tg`. Remove it before you
install tgcli. In the other order, uninstalling it deletes the new `tg` link.

```bash
export PATH="$HOME/.local/bin:$PATH"
if uv tool list 2>/dev/null | grep -q '^tg-harness '; then uv tool uninstall tg-harness; fi
if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^tg-harness '; then pipx uninstall tg-harness; fi
```

This removes the program only. Leave its login in `~/.config/tg/` and
`~/.local/state/tg/` untouched. tgcli does not read those folders.

## 4. Install tgcli

The block installs the current commit of `main` from a GitHub archive, so it
needs no `git`. It fetches `SKILL.md`, the guide to using `tg`, from the same
commit. The `fast` extra adds native encryption that makes large media
transfers much faster. Intel Macs skip it because it has no build for them.

```bash
export PATH="$HOME/.local/bin:$PATH"
sha=$(curl -fsSL -H 'Accept: application/vnd.github.sha' https://api.github.com/repos/speech115/tgcli/commits/main) || sha=main
case "$(uname -sm)" in
  "Darwin x86_64") extra="" ;;
  *) extra="[fast]" ;;
esac
uv tool install --force "tgcli$extra @ https://github.com/speech115/tgcli/archive/$sha.zip"
uv tool update-shell
mkdir -p "$HOME/.agents/skills/tgcli"
curl -fsSL "https://raw.githubusercontent.com/speech115/tgcli/$sha/SKILL.md" -o "$HOME/.agents/skills/tgcli/SKILL.md"
tg --version
command -v tg
```

Check the output:

- `tg --version` prints a version number such as `5.0.0`.
- `command -v tg` prints the path in `uv tool dir --bin`, usually
  `~/.local/bin/tg`. If it prints another path, an older `tg` comes first.
  Find out which program it belongs to with `ls -l` on that path. Remove that
  program with the tool that installed it, then repeat step 4.
- If `tg --version` fails with `unrecognized arguments: --version`, the old
  tg-harness still comes first. Go back to step 3.

## 5. Connect the Telegram account

First check whether an account is already connected:

```bash
export PATH="$HOME/.local/bin:$PATH"
tg --json doctor --connect
```

If the output has `"ok": true` and a `user` for the account, skip to step 6.
Otherwise, tell the person in one sentence what is about to happen. For
example:

> Сейчас в браузере откроется страница tgcli. Сделайте шаги на ней: создайте
> свои ключи Telegram на my.telegram.org и отсканируйте QR-код в Telegram на
> телефоне.

> A tgcli page is opening in your browser. Follow its steps: create your own
> Telegram keys on my.telegram.org, then scan the QR code with Telegram on
> your phone.

Then run:

```bash
export PATH="$HOME/.local/bin:$PATH"
tg setup
```

- `tg setup` serves the page on this computer, opens it in the browser and
  prints its address on stderr. If no browser window opened, give the person
  that address.
- The command waits until the person finishes the page, which can take
  several minutes. Start it so that your shell tool does not stop it early.
  In Claude Code, run it in the background and wait for it. In other agents,
  use the longest timeout your shell tool allows. If your tool stopped it
  anyway, run `tg setup` once more.
- Exit 0 means the account is connected.
- Exit 2 with `setup was cancelled in the browser`: the person pressed
  Cancel. Do not start it again on your own; ask whether they want to try now.
- Exit 2 with `another tg setup is already running`: a page is already open.
  Ask the person to finish or close it, then go back to the start of this
  step: `tg doctor --connect` shows whether that page already connected the
  account.
- Exit 2 with any other `BLOCKED` message means `TGCLI_READONLY=1` is set in
  your environment. Run that one command as `TGCLI_READONLY=0 tg setup`.
- `invalid choice: 'setup'` means the installed tgcli is older than `tg setup`.
  Run step 4 again, once. If the error stays, stop and tell the person.
- On any other exit code, tell the person what the error message says in
  plain words, then run `tg setup` once more. If it fails again, stop and tell
  the person what failed.

## 6. Connect your agents

The person may use more than one AI app. Set up `tg` for each one installed on
this computer, so that a new chat in any of them knows about `tg`.

Claude Code gets the tgcli plugin. It carries the skill and a check at the
start of each session that says when `tg` is missing or not logged in. If the
plugin cannot be installed, Claude Code gets a copy of the skill instead.

```bash
export PATH="$HOME/.local/bin:$PATH"
claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
if command -v claude >/dev/null 2>&1 \
  && claude plugin marketplace add speech115/tgcli \
  && claude plugin marketplace update tgcli \
  && claude plugin install tgcli@tgcli \
  && claude plugin update tgcli@tgcli; then
  echo "claude code: plugin tgcli@tgcli"
elif [ -d "$claude_dir" ]; then
  mkdir -p "$claude_dir/skills/tgcli"
  cp "$HOME/.agents/skills/tgcli/SKILL.md" "$claude_dir/skills/tgcli/SKILL.md"
  echo "claude code: skill $claude_dir/skills/tgcli"
fi
```

The next block sets up the other agents. It acts only on agents whose folders
exist:

- `~/.agents/skills/tgcli`, written in step 4, is the skill for Codex and
  Gemini CLI. Antigravity gets a copy in `~/.gemini/config/skills/tgcli`.
- A short instructions block, between `tgcli:begin` and `tgcli:end` markers,
  goes into each global instructions file: `~/.claude/CLAUDE.md`, Codex's
  `~/.codex/AGENTS.md` (or a non-empty `AGENTS.override.md`), and
  `~/.gemini/GEMINI.md`, which Gemini CLI and Antigravity both read. A rerun
  replaces the block.
- Codex gets `~/.codex/rules/tgcli.rules`, which lets `tg` run outside the
  sandbox without a prompt, except `send`, `edit`, `delete` and `forward`
  with `--commit`: Codex asks the human before each of those. In `~/.codex/config.toml` it gets a
  `[sandbox_workspace_write]` table that allows the network and writes to
  `~/.config/tgcli` and `~/.local/state/tgcli`. The block saves a backup of
  `config.toml` before it changes the file. It restores the backup if the
  result does not parse.

```bash
export PATH="$HOME/.local/bin:$PATH"
TG_BIN="$(uv tool dir --bin)/tg" uv run --no-project --python ">=3.11" python - <<'PY'
import json
import os
import re
import shutil
import time
import tomllib
from pathlib import Path

home = Path.home()
skill = (home / ".agents/skills/tgcli/SKILL.md").read_text()
tg = os.environ["TG_BIN"]
if not os.access(tg, os.X_OK):
    raise SystemExit(f"{tg} is missing: finish step 4 first")
block = (
    "<!-- tgcli:begin -->\n"
    "## Telegram\n\n"
    "For any Telegram task use `tg` (tgcli). Read the tgcli skill first: "
    "`~/.agents/skills/tgcli/SKILL.md`. Never use other Telegram tools. "
    "The old speech115/tg (tg-harness) is retired. If `tg` is missing or "
    "not logged in, follow "
    "https://github.com/speech115/tgcli/blob/main/INSTALL_FOR_AGENTS.md.\n"
    "<!-- tgcli:end -->\n"
)
marked_md = re.compile(r"<!-- tgcli:begin -->\n.*?<!-- tgcli:end -->\n?", re.S)


def put_block(path):
    old = path.read_text() if path.exists() else ""
    if marked_md.search(old):
        new = marked_md.sub(lambda _: block, old, count=1)
    else:
        new = old.rstrip("\n") + "\n\n" + block if old.strip() else block
    path.parent.mkdir(parents=True, exist_ok=True)
    if new != old:
        path.write_text(new)
    print("instructions:", path)


def put_skill(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill)
    print("skill:", path)


claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
if claude.is_dir():
    put_block(claude / "CLAUDE.md")

gemini = home / ".gemini"
if gemini.is_dir():
    put_block(gemini / "GEMINI.md")
    put_skill(gemini / "config/skills/tgcli/SKILL.md")

codex = Path(os.environ.get("CODEX_HOME", home / ".codex"))
if codex.is_dir():
    override = codex / "AGENTS.override.md"
    has_override = override.exists() and override.read_text().strip()
    put_block(override if has_override else codex / "AGENTS.md")
    rules = codex / "rules/tgcli.rules"
    rules.parent.mkdir(parents=True, exist_ok=True)
    tgs = f'["tg", {json.dumps(tg)}]'
    writes = '["send", "edit", "delete", "forward"]'
    ask = 'decision="prompt", justification="sends, edits or deletes a Telegram message")\n'
    rules.write_text(
        f'prefix_rule(pattern=[{tgs}], decision="allow", '
        'justification="tgcli needs the network and writes ~/.config/tgcli '
        'and ~/.local/state/tgcli")\n'
        f'prefix_rule(pattern=[{tgs}, "--json", {writes}, "--commit"], {ask}'
        f'prefix_rule(pattern=[{tgs}, {writes}, "--commit"], {ask}'
    )
    print("codex rule:", rules)
    config = codex / "config.toml"
    old = config.read_text() if config.exists() else ""
    roots = [str(home / ".config/tgcli"), str(home / ".local/state/tgcli")]
    marked_toml = re.compile(r"(?ms)^# tgcli:begin\n.*?^# tgcli:end\n?")
    rest = marked_toml.sub("", old)
    try:
        tomllib.loads(old)
        table = tomllib.loads(rest).get("sandbox_workspace_write")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"codex: {config} does not parse ({exc}); fix it first")
    ours = (
        "# tgcli:begin\n[sandbox_workspace_write]\n"
        f"writable_roots = {json.dumps(roots)}\nnetwork_access = true\n"
        "# tgcli:end\n"
    )
    if table is None:
        new = (rest.rstrip("\n") + "\n\n" if rest.strip() else "") + ours
    elif set(roots) <= set(table.get("writable_roots", [])) and table.get(
        "network_access"
    ) is True:
        new = rest
    else:
        new = old
        print(
            f"codex: merge by hand. In [sandbox_workspace_write] of {config}, "
            f"add {json.dumps(roots)} to writable_roots and set "
            "network_access = true."
        )
    if new != old:
        backup = config.with_name(f"config.toml.bak-tgcli-{time.strftime('%Y%m%d-%H%M%S')}")
        if config.exists():
            shutil.copy2(config, backup)
        config.write_text(new)
        try:
            check = tomllib.loads(config.read_text())["sandbox_workspace_write"]
            assert set(roots) <= set(check["writable_roots"])
            assert check["network_access"] is True
        except Exception:
            if backup.exists():
                shutil.copy2(backup, config)
            raise
        print("codex config:", config, "backup:", backup if backup.exists() else "none")
PY
```

If it prints `codex: merge by hand`, the person's `config.toml` already has its
own `[sandbox_workspace_write]` table. Edit that table as the message says,
keep everything else, and check that the file still parses.

Agents load skills and instructions when a chat starts. Claude Code loads a
new plugin in a new session or after `/reload-plugins`. Codex reads
`config.toml` and its rules only when it starts.

## 7. Check and report

```bash
export PATH="$HOME/.local/bin:$PATH"
tg --json doctor --connect
tg --readonly --json dialogs --limit 3
```

- `doctor` reports `"ok": true` and the account's `user`.
- `dialogs` exits 0 and lists up to three chats. A new account may have
  fewer; that is fine.

Tell the person, in plain words:

- that tgcli is installed and which Telegram account is connected;
- the names of the chats it listed, as proof that it works;
- which AI apps now know about `tg`;
- that they should start a new chat, and restart Codex or Antigravity if they
  use it;
- one example of what to ask, such as «Что у меня непрочитанного в Telegram?».

If a check failed, say what failed in one sentence and what you will do next.
Then do it.

## Update

Run steps 2 to 4, step 6 and step 7 again. They install the current `main`,
refresh the skill and the instruction blocks, and keep the login.

## Uninstall

Run this block only when the person asks to remove tgcli. It removes the
program, the plugin, the skills, the instruction blocks and the Codex settings.

```bash
export PATH="$HOME/.local/bin:$PATH"
uv tool uninstall tgcli
if command -v claude >/dev/null 2>&1; then
  claude plugin uninstall tgcli@tgcli
  claude plugin marketplace remove tgcli
fi
rm -rf "$HOME/.agents/skills/tgcli" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/tgcli" \
  "$HOME/.gemini/config/skills/tgcli" "${CODEX_HOME:-$HOME/.codex}/rules/tgcli.rules"
uv run --no-project --python ">=3.11" python - <<'PY'
import os
import re
from pathlib import Path

home = Path.home()
claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
codex = Path(os.environ.get("CODEX_HOME", home / ".codex"))
markdown = re.compile(r"<!-- tgcli:begin -->\n.*?<!-- tgcli:end -->\n?", re.S)
toml = re.compile(r"(?ms)^# tgcli:begin\n.*?^# tgcli:end\n?")
targets = [
    (claude / "CLAUDE.md", markdown),
    (codex / "AGENTS.md", markdown),
    (codex / "AGENTS.override.md", markdown),
    (home / ".gemini/GEMINI.md", markdown),
    (codex / "config.toml", toml),
]
for path, marked in targets:
    if not path.exists():
        continue
    old = path.read_text()
    rest = marked.sub("", old)
    if rest == old:
        continue
    if rest.strip():
        path.write_text(rest.rstrip("\n") + "\n")
    else:
        path.unlink()
    print("removed the tgcli block:", path)
PY
```

The login stays in `~/.config/tgcli/` and `~/.local/state/tgcli/`. Delete those
two folders only if the person asks for that too. To end the session on
Telegram's side, the person opens Telegram, **Settings**, **Devices**, and ends
the session named **tgcli**.

## Troubleshooting

| What you see | What to do |
|---|---|
| `uv: command not found` or `tg: command not found` | Put `export PATH="$HOME/.local/bin:$PATH"` at the top of the command. |
| `tg --version` fails with `unrecognized arguments: --version` | The old tg-harness comes first on PATH. Run step 3, then step 4. |
| Exit 3 with `CONFIG` | No account is connected yet. Run step 5. |
| Exit 3 with `needs reauthentication` | The session ended, for example from Telegram's **Devices** list. Run `tg setup` again. |
| Codex: `Operation not permitted`, or no connection to Telegram | The sandbox blocked `tg`. Run the command outside the sandbox now. Step 6 fixes new Codex sessions. |
| Antigravity: no connection to Telegram | Its sandbox has no network. Ask to run `tg` outside the sandbox. |
| Exit 5 with `retry_after` | Telegram asked to slow down. Wait that many seconds, then run the command once more. |

Commands, exit codes and safety rules for everyday use are in
[SKILL.md](SKILL.md) and [docs/CONTRACT.md](docs/CONTRACT.md).
