# eureka-config

My dotfiles for macOS and Fedora, applied with [chezmoi](https://chezmoi.io).

## Install

The clone goes over SSH, so the machine needs an SSH identity GitHub accepts.
uv comes first: chezmoi compiles the package inventory with `uv run` before it
reads anything else.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh    # then open a new shell
brew install chezmoi                               # macOS; git and make come with Xcode's CLT
sudo dnf install -y chezmoi git make               # Fedora
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
mise install
```

On Fedora, log out and back in for zsh to become the login shell.

## Day to day

Edit, then `chezmoi apply`; other machines pick changes up with `chezmoi update`.

```sh
mise install    # in this repo: the pinned lint tools and the git hooks
make test       # the test suite; PYTEST_ARGS='-k gate' to narrow
make check      # every prek hook, then the tests
```

A commit runs the prek hooks. The tests run in CI, on macOS and in a Fedora
container.

## Where things live

| Path | What |
| ---- | ---- |
| `.chezmoidata/packages.toml` | every tool, one table each |
| `.chezmoidata/packages.local.toml` | this machine's extra packages (gitignored) |
| `tools/inventory/` | the inventory compiler; its types generate `.schema/` |
| `dot_claude/` | Claude Code: CLAUDE.md, hooks, skills, agents |
| `claude-managed.d/` | the parts of `~/.claude/settings.json` chezmoi owns ([semantics](tools/claude_managed.py)) |
| `claude/mods/` | Claude Code mods, linked to `~/.claude/mods` ([README](claude/mods/README.md)) |
