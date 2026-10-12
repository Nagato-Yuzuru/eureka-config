# eureka-config

## Install

Both paths clone over SSH, so the machine needs an SSH identity GitHub
accepts. Accept github.com's host key when the first clone asks; later SSH
clones (externals) are covered by `run_once_before_trust-github-ssh.sh`.

### macOS

```sh
brew install chezmoi
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
mise install
```

### Fedora

```sh
sudo dnf install -y chezmoi git zsh
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
```

Log out and back in for the login shell to become zsh, then `mise install`.

In a VM whose platform supplies its own kernel, `kernel-devel` never matches
the running kernel. The eBPF packages already assume that (see `packages.toml`).

## The loop

Edit, then `chezmoi apply`. chezmoi runs `make` before every command that reads
the source state, and make reruns the inventory compiler when an input moved.
There is no separate build step.

```sh
make                     # rebuild .rendered/ and .schema/ by hand
uv run render schema     # only .schema/; works while a data file is broken
make check               # every check: lint (all prek hooks) then test
make test                # pytest only, ~20 s; PYTEST_ARGS='-k gate' to narrow
prek install             # the git hooks in .pre-commit-config.yaml
```

Changes reach other machines through git: commit, push, `chezmoi update` on
the target.

## Add or remove a tool

`.chezmoidata/packages.toml` is the only place a tool is declared. One table
answers three questions: where it goes (`on`), how it gets there (`via` and
that backend's block), and which zsh completions it generates (`completions`).

```toml
[packages.git]
on = ["darwin", "linux"]

[packages.fd]
on = ["darwin", "linux"]
dnf = { name = "fd-find" }

[packages.starship]
on = ["darwin", "linux"]
dnf = { copr = "atim/starship" }
completions = { starship = "starship completions zsh" }

[packages.ghostty]
on = ["darwin"]
brew = { cask = "ghostty" }

[packages.rust]
on = ["darwin", "linux"]
via = "mise"
mise = { version = "stable", components = ["rust-analyzer", "rust-src"] }

[packages.pnpm]
on = ["darwin", "linux"]
via = "mise"
overrides = [{ when = "darwin/amd64", mise = { name = "npm:pnpm" } }]

[packages.ruff]
on = ["darwin", "linux"]
via = "uv"
completions = { ruff = "ruff generate-shell-completion zsh" }
```

Picking a backend: one global version → system package. Multiple versions →
mise. A Python CLI → uv. Exception: uv itself must stay a system package,
because chezmoi's install script needs it before mise runs.

**Add:** write the table, `chezmoi apply`. mise tools also need `mise install`.
If no package manager ships the tool's completion, add a `completions` recipe.

**Remove:** delete the table, `chezmoi apply`. Leftovers differ by backend:

- mise: drops off PATH immediately; versions stay on disk until `mise prune`.
- brew / dnf / uv: only ever install; manual uninstall required.
- Generated completions disappear on the same apply.

### Packages only this machine has

`.chezmoidata/packages.local.toml` is gitignored and written like
`packages.toml` (same tables, same schema line: `#:schema ../.schema/packages.json`).
It works like `mise.local.toml`: a key it declares replaces that entry of
`packages.toml` whole, and any other key is added. It lives only on this
machine, so nothing backs it up, and `git clean -x` deletes it.

### The fields

| Field | What |
| ----- | ---- |
| `on` | Machine tags: `darwin`, `linux`, or `os/arch`. Open set. |
| `via` | Backend discriminator. `system` (default) = platform package manager per `platforms.toml`. `mise` and `uv` allow exactly their own block. |
| `brew` | `{ name \| cask }` — read only on macOS. |
| `dnf` | `{ name }` + at most one source: `copr` or `repofile`. |
| `mise` | `{ name \| version \| components }` — `components` for things like rust-analyzer that rustup's default profile omits. |
| `uv` | `{ name \| version }` |
| `overrides` | Per-machine deep-merge: `[{ when = "os/arch", ... }]`. May touch `via` and backend blocks, never `on` or `completions`. |
| `completions` | `{ command = "shell expression" }` — generates a `#compdef` file. |

`name` is the name on that backend when it differs from the table key.
`version` uses the backend's own syntax (`stable`, `lts`, `0.15.21`). A block
on the wrong `via` is a type error, not an ignored field.

### Schema and validation

Types live in `tools/inventory/model.py` (Pydantic). The same types produce
`.schema/<file>.json` — the `#:schema` line in each `.toml` points there, so
taplo/tombi validate against the same constraints `render` enforces.

`.rendered/` is build output (gitignored); `.schema/` is committed. A stale
`.rendered/` is caught by the artifact template and fixed with `make`.

## Zsh completions

`packages.toml` carries completion recipes in the `completions` field. Only
tools whose completion nobody else installs need a recipe.

- `mise run refresh-completions` rebuilds the generated completions dir from
  scratch. Runs automatically: mise's `postinstall` hook triggers it after
  `mise install`; chezmoi's `run_onchange_after_refresh-completions` triggers
  it when the recipe list changes.
- The generated dir goes **last** on fpath, so a completion the package manager
  ships always wins. A recipe is safe to declare on any tool — where
  brew/dnf already ship the file, the generated one is a shadowed no-op.
- `mise run audit-completions` reports coverage and shadowing. Run it before
  adding or doubting a recipe.
- `clap_stub <cmd> '<generator>'` wraps a clap dynamic completer
  (`COMPLETE=zsh prek`) so it works from a file.
- terraform and tofu use bash's `complete -C` protocol via bashcompinit in
  `.zshrc`; `sg` borrows `_ast-grep` there too.
- `~/.local/share/zsh/site-functions` stays first on fpath for anything
  dropped by hand.
- Wiped `$XDG_CACHE_HOME` → `mise run refresh-completions` repairs.

## Machines and platforms

- Another os/arch of an existing platform: one line in `machines.toml`.
- A new platform (new OS + package manager): an entry in `platforms.toml` plus
  its backend in `model.py` and `render.py`.
- A specific device is not expressible yet — the artifact template picks by
  `<os>-<arch>` alone.

## Layout

| Path | What |
| ---- | ---- |
| `.chezmoidata/packages.toml` | every tool, one table each |
| `.chezmoidata/platforms.toml` | OS → system package manager |
| `.chezmoidata/machines.toml` | render targets, `<os>-<arch>` |
| `.chezmoidata/xdg.toml` | XDG base dirs |
| `.chezmoidata/zsh.toml` | generated-completions dir path |
| `tools/inventory/` | inventory compiler: types, resolution, renderers |
| `.rendered/<machine>/` | compiled artifacts (gitignored) |
| `.schema/` | JSON Schema from the types (committed) |
| `Makefile` | rebuilds `.rendered/` and `.schema/`; chezmoi's hook runs it |
| `.chezmoitemplates/artifact` | includes this machine's artifact; refuses stale trees |
| `.chezmoiexternal.toml.tmpl` | oh-my-tmux and antidote as git clones |
| `run_once_before_trust-github-ssh.sh` | pins github.com host keys |
| `run_onchange_before_install-*.sh` | installs system packages and uv tools on set change |
| `run_once_after_setup-linux.sh` | Fedora bootstrap: login shell, git identity |
| `run_onchange_after_install-yazi-*` | `ya pkg install` on `package.toml` change |
| `run_onchange_after_refresh-*.sh` | regenerates zsh completions on recipe list change |
| `dot_config/mise/tasks/` | `refresh-completions`, `audit-completions` |
| `dot_claude/` | Claude Code: CLAUDE.md, hooks, skills, the settings.json merge |
| `claude-managed.d/` | managed-settings fragments, rendered into the merge (not deployed) |
| `claude/` | Claude Code mods, developed here, linked to `~/.claude/mods` (not deployed) |
| `tests/` | pytest for the inventory and settings merge engine |

## Claude Code managed settings

`~/.claude/settings.json` is co-owned: chezmoi manages a declared subset via
per-feature fragments in `claude-managed.d/`, everything else belongs to Claude
Code and the user. The fragments are not deployed: chezmoi renders them into
`dot_claude/modify_settings.json.tmpl` (the merge) and
`run_onchange_after_snapshot-claude-managed.py.tmpl` (the three-way-merge base),
so `chezmoi diff` shows what a fragment edit does to `settings.json`.

### Operations

- **Add a feature:** new `<feature>.json.tmpl` with `$merge` declarations.
- **Remove a feature:** delete its source file. Managed hooks and list elements
  vanish on the next apply; user-modified values are left with a warning.
- **Scope to one platform:** split into `<feature>.json` and
  `<feature>-<os>.json.tmpl`, the second rendering `{}` off its platform.
- **Resolve a conflict:** `chezmoi apply` exits 1 when the live value differs
  from what we own. To keep the live value, align the fragment. To take ours, paste the `CLAUDE_MANAGED_FORCE='...'`
  command from the error output.

### Fragment `$merge` strategies

| Strategy | Meaning |
| -------- | ------- |
| `exact` | Atomic leaf (deep equality). Default for non-objects. |
| `ensure` | Three-way element set: foreign elements kept, retired ones removed. |
| `keyed` | List entries claimed by hook-command basename; foreign entries pass through. |

`$legacyHooks` lists retired hook basenames to purge.

Full semantics: docstring of `tools/claude_managed.py`, tested in `tests/`.

### Plugins

`plugins.json` holds plugins for every machine; `plugins-darwin.json.tmpl` holds
macOS-only ones. Add a plugin id to the right file, `chezmoi apply`. Delete
the line and the key is retired on the next apply.

Gotcha: `chezmoi apply` declares the plugin but does not install it. On a new
machine, run the `claude plugin install` line Claude Code prints.

Our own mods live in `claude/mods/`, linked to `~/.claude/mods` and enabled by
`claude-managed.d/local-mods.json.tmpl`; [claude/mods/README.md](claude/mods/README.md)
has the layout and the change, check and debug steps.

Not managed on purpose: `~/.claude/local-plugins/` (would produce errors on
machines without it), plugins set to `false` (the UI already holds that state).
