# eureka-config

One chezmoi source for the machines I actually use: an Apple-silicon Mac, an
Intel Mac, and a Fedora VM in OrbStack (`linux-lab`, for eBPF study). Shell
(zsh, antidote, starship), tmux on top of oh-my-tmux, mise, Claude Code
settings, and the list of tools each machine gets. Neovim lives in its own
repo.

Everything renders per machine from `.chezmoi.os` / `.chezmoi.arch`, so the
same commit produces a Mac and a Fedora home directory. There is no machine
identity beyond os and arch yet; the three machines happen to differ there.

## Layout

| Path                                | What                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------ |
| `.chezmoidata/packages.toml`        | every tool, one table each, whatever installs it (see below)                               |
| `.chezmoidata/platforms.toml`       | kinds of machine: OS and the system package manager that comes with it                     |
| `.chezmoidata/machines.toml`        | the render targets, `<os>-<arch>` today; a specific device would be one more entry         |
| `.chezmoidata/xdg.toml`             | XDG base dirs; `.zshenv` exports them, externals and symlinks derive their paths from them |
| `.chezmoidata/zsh.toml`             | the generated-completions dir, declared once for `.zshrc` and both mise tasks              |
| `tools/inventory/`                  | the inventory compiler: types, resolution, renderers; `uv run render` writes `.rendered/`  |
| `.rendered/<machine>/`              | the compiled artifacts chezmoi includes (Brewfile lines, mise tools, recipes …), committed |
| `.rendered/schema/`                 | JSON Schema per data file, generated from the same types; each `.toml` names its own       |
| `.chezmoitemplates/artifact`        | includes this machine's artifact; refuses a stale tree or an undeclared machine            |
| `.chezmoiexternal.toml.tmpl`        | oh-my-tmux and (linux) antidote as git clones under `$XDG_DATA_HOME`                       |
| `.chezmoiignore`                    | what a non-Mac never gets: Brewfile, ghostty, wezterm, 1Password                           |
| `run_onchange_before_install-*.sh`  | installs the system packages (brew bundle / dnf) and uv tools whenever this machine's set changes |
| `run_once_after_setup-linux.sh`     | Fedora bootstrap: login shell, git include and identity                                    |
| `run_onchange_after_install-yazi-*` | `ya pkg install` whenever yazi's `package.toml` (a rev+hash lockfile) changes              |
| `run_onchange_after_refresh-*.sh`   | regenerates the zsh completions whenever this machine's recipe list changes (below)        |
| `dot_config/mise/tasks/`            | mise file tasks rendered per machine: `refresh-completions`, `audit-completions` (below)   |
| `dot_claude/`                       | Claude Code: CLAUDE.md, hooks, skills, and the managed-settings fragments (last section)    |
| `tests/`                            | pytest for the inventory and the settings merge engine; never deployed                     |

## Packages

`packages.toml` is the inventory. One table per tool answers three questions:
where it goes (`on`), how it gets there (`via` and that backend's block), and
which zsh completions it generates (`completions`).

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

- `on` is a list of machine tags: `darwin`, `linux`, or `os/arch`. (An open
  set: a platform or device selector is one more variant, existing lines do
  not change.)
- `via` picks the backend and is the discriminator for the rest of the line.
  `system` (the default) means the machine's platform's package manager —
  brew on macOS, dnf on Fedora, per `platforms.toml`, nowhere else — and
  allows both a `brew:` and a `dnf:` block, each read only where it applies.
  `mise` and `uv` allow exactly their own block. The rule I use: one global
  version, system package; multiple versions, mise; a Python CLI, uv. uv itself
  is a system package on both: it never needs more than one version, chezmoi's
  own scripts need it before mise has installed anything, and that is what
  lets the install script run `uv tool install` right after brew/dnf.
- The blocks carry what only that backend understands: `brew: { name | cask }`,
  `dnf: { name }` plus at most one source (`copr` | `repofile`),
  `mise: { name | version | components }`,
  `uv: { name | version }`. `name` is the name on that backend when it differs
  from the key. `version` is in the backend's own syntax (`stable`, `lts`,
  `0.15.21`); declaring it and spelling it into a command are separate jobs,
  the second is each renderer's. rust-analyzer and rust-src ride on the rust
  toolchain as `components` — rustup's default profile ships neither, so
  without the line a new machine gets no language server. A block on the wrong
  `via`, or a `version` outside a block, is a type error, not an ignored field.
- `overrides` is the only per-machine mechanism. Each entry whose `when` tag
  matches this machine is deep-merged over the line, in order (maps merge,
  lists and scalars replace); an entry that switches `via` starts the how
  afresh, and one that sets a union block's deciding field (`cask`, `copr`,
  `repofile`) switches that block's variant instead of piling up. It may
  touch `via` and the blocks, never `on` or `completions`; today only pnpm
  uses one.
- `completions` maps a command to the shell command that prints its zsh
  completion. See the next section.

The types are the schema: `tools/inventory/model.py` (Pydantic) says exactly
what a table may contain, `inventory.py` resolves it per machine, `render.py`
turns the resolved list into text. `uv run render` compiles every machine in
`machines.toml` into `.rendered/<machine>/{brewfile,dnf-list,dnf-repos,mise-tools,uv-tools,completions}`
plus a `STAMP` (the hash of the three data files), and the same types into
`.rendered/schema/<file>.json` — a JSON Schema per data file, which the
`#:schema` line at the top of each `.toml` points at, so a TOML language
server (taplo, tombi) completes and checks the file with the constraints
`render` enforces (`deno run npm:@taplo/cli lint .chezmoidata/*.toml` does
the same from a shell). The tree is committed, and `prek` refuses a commit
where it is stale. chezmoi never runs Python: its
templates `include` the file for `<os>-<arch>` through `.chezmoitemplates/artifact`,
which recomputes the hash and fails on a tree compiled from other data than
what is checked out, or on a machine `machines.toml` does not declare. Bad data
fails `render` with a message naming the entry (`packages.rust.mise.components:
must be a non-empty list`). A diff under `.rendered/` shows exactly which
machine an edit touched.

Each layer has one owner and one trigger: chezmoi installs the system packages
and uv tools when this machine's set changes; `mise install` is a manual step
and stays one. Not modelled yet: pnpm globals. When the first one is declared,
`via: pnpm` takes the same shape — one more package variant and renderer, and a
rendered mise task run by hand after `mise install` (pnpm is a mise tool),
`depends_post` on `refresh-completions`, no hook.

## Zsh completions

Completion definitions come from four layers, none of them hand-written here:
zsh's own compsys (`_git _tmux _jq`), what brew formulae and Fedora rpms
install alongside the tool (`_gh _bat _fd _rg _uv …`), what the tool generates
itself (`kubectl completion zsh`, `COMPLETE=zsh prek`), and
zsh-users/zsh-completions (`_golang _node _tsc _cmake`). The third layer is the
only one nobody installs for us — mise tools, uv tools, casks — and
`packages.toml` carries its recipes. A tool that is not in `packages.toml` has
no completion here, and should not need one:

```yaml
kubectl: { on: [darwin], via: mise, completions: { kubectl: "kubectl completion zsh" } }
ruff:    { on: [darwin, linux], via: uv, completions: { ruff: "ruff generate-shell-completion zsh" } }
mise:    { on: [darwin, linux], …,   completions: { mise: "mise completion zsh" } }  # jdx's rpm ships none
```

- `mise run refresh-completions` (rendered per machine, so it only knows this
  machine's recipes) rebuilds the generated dir (`zsh.generatedCompletions` in `.chezmoidata/zsh.toml`, under `$XDG_CACHE_HOME`) from scratch:
  skips commands not installed, runs each recipe with stdin closed, checks the
  output starts with `#compdef`, then swaps the directory in and drops
  `~/.zcompdump`. A tool that leaves `packages.toml` leaves no file behind.
- Two things make the generated set stale, and each has its own trigger: a
  tool binary changed — mise's `postinstall` hook runs the task after every
  `mise install` / `mise up`; the recipe list changed — chezmoi's
  `run_onchange_after_refresh-completions` runs it whenever this machine's
  rendered recipes differ (the list is rendered into the script for that
  alone). On a new machine that script runs before `mise install` has, sees an
  incomplete toolset and defers to the hook. Nothing runs at shell startup. A
  wiped `$XDG_CACHE_HOME` is a `mise run refresh-completions` by hand — it is a
  cache.
- `.zshrc` puts that directory **last** on fpath. compinit takes the first
  `_<cmd>` it finds, so a completion the package manager ships always beats a
  generated one — which is what makes a recipe safe to declare on any tool:
  where brew or dnf already ship it, the file is a shadowed no-op; where they
  don't (Fedora's mise and starship rpms), it fills the gap. Nothing in
  `packages.toml` records who ships what, and `completions` cannot be
  overridden per machine, because it never needs to be.
- `clap_stub <cmd> '<generator>'` in a recipe wraps a clap dynamic completer
  (`COMPLETE=zsh prek`) whose script must be sourced, so it works from a file.
- `mise run audit-completions` reports, for this machine, which packages ship
  their own completion, who completes every command mise puts on PATH and
  every entry point of the uv tools, and whether each recipe is what actually
  completes here or is shadowed — the check to run before adding or doubting a
  recipe.
- Not files: terraform/tofu only speak bash's `complete -C` protocol and are
  wired through bashcompinit in `.zshrc`; `sg` borrows `_ast-grep` there too.
- `~/.local/share/zsh/site-functions` stays first on fpath for anything dropped
  by hand; the generator never touches it.

## New machine

Mac (Homebrew and 1Password's SSH agent already set up):

```sh
brew install chezmoi
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
mise install   # its postinstall hook generates the zsh completions too
```

Fedora VM in OrbStack (the SSH agent is forwarded from the Mac):

```sh
orb create fedora linux-lab
orb -m linux-lab sudo dnf install -y chezmoi git zsh
orb -m linux-lab chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
```

Accept github.com's host key when the first clone asks. Log out and back in
for the login shell to become zsh, then `mise install` inside the VM. While
iterating on the source from the Mac, apply the working tree straight into
the VM without committing:

```sh
orb -m linux-lab chezmoi apply --source /mnt/mac/Users/yuzuru/.local/share/chezmoi
```

`orb` keeps the Mac's working directory (mounted at `/mnt/mac`), and mise
treats `<dir>/.config/mise/config.toml` of every ancestor as a project config:
run `mise …` in the VM from a VM path (`orb -m linux-lab zsh -lc 'cd ~ && mise …'`),
or from under `/mnt/mac/Users/yuzuru` it loads the Mac's global config and
installs the Mac's tool set into the VM.

The VM runs OrbStack's kernel, not Fedora's, so `kernel-devel` never matches and
the eBPF section of `packages.toml` takes the BTF/CO-RE path throughout.

## Development

```sh
uv run render            # after editing .chezmoidata/*.toml or tools/inventory; commit .rendered/ with it
uv run render schema     # only .rendered/schema/ — from the types alone, works while a data file is broken
uv run render --check    # what prek runs: fails on a stale tree
uv run pytest            # everything, ~15 s
prek install             # the git hooks in .pre-commit-config.yaml
```

What another machine gets is a file: `cat .rendered/darwin-amd64/mise-tools`.

## Claude Code managed settings: the fragment protocol

`~/.claude/settings.json` is co-owned: chezmoi manages a declared subset of it,
everything else belongs to Claude Code and the user. The subset is declared as
**per-feature fragments** and merged in, never templated wholesale.

```
dot_claude/exact_claude-managed.d/<feature>.json.tmpl   source, one file per feature
        │ chezmoi apply (exact_: deleting a source file deletes its fragment)
        ▼
~/.claude/claude-managed.d/<feature>.json               rendered fragments
        │ dot_claude/modify_settings.json               compose + three-way merge
        ▼
~/.claude/settings.json                                 live settings (co-owned)

~/.claude/.claude-managed.applied.json                  merge base: the composed
                                                        fragment as of the last
                                                        converged apply, advanced by
                                                        run_onchange_after_snapshot-*
```

### Fragment format

A fragment is a JSON object shaped exactly like the `settings.json` subset it
owns, plus reserved `$`-prefixed top-level keys:

- `$merge` declares `{"dotted.path": strategy}` for the paths this file
  owns (`*` matches exactly one segment). Ownership semantics are **declared,
  never inferred from data shape**:

  | strategy | meaning                                                                                                                    |
  | -------- | -------------------------------------------------------------------------------------------------------------------------- |
  | `exact`  | the value is one atomic leaf (deep equality). Default for every non-object; declare on an object to own the whole subtree. |
  | `ensure` | list as a three-way element set: foreign elements kept, our retired elements removed, missing ones appended.               |
  | `keyed`  | list entries claimed by hook-command basename: ours replaced/retired, foreign entries pass through.                        |

  Objects without a declaration recurse per key.

- `$legacyHooks` lists retired hook basenames to purge (union across fragments).

### Invariants

- **Declarative in all three directions.** Add/change a fragment → converges;
  delete a fragment file (or a path in one) → the slice is _retired_ from
  settings on the same apply, as long as it still equals the base.
- **Fail-safe against the outside world.** A foreign edit to a value we own is
  a conflict: reported on stderr, `chezmoi apply` exits 1, settings untouched.
  A foreign edit to a value we _retired_ is kept with a warning. Ownership
  was withdrawn, so it never blocks. Nothing foreign is ever silently
  overwritten; without a base the engine errs toward conflicts.
- **Fragments must be self-consistent.** Cross-fragment value collisions,
  strategy contradictions (overlapping or nested patterns), duplicate claim
  keys and ensure elements, and unknown `$` keys are compose errors, all
  reported at once, apply refused. They surface on `chezmoi apply`: the filter
  composes the _deployed_ fragments, so a contradiction in freshly edited
  sources shows up when apply deploys them and then refuses the settings
  target.
- **Formatting-stable.** A no-op merge passes the original bytes through, so
  `chezmoi diff` never shows pure formatting churn.

### Operations

- **Add a feature**: new `<feature>.json.tmpl` with its own `$merge`
  declarations. Same-path declarations across files must agree.
- **Remove a feature**: delete its source file. Its hooks/list elements vanish
  on the next apply; anything the user modified meanwhile is left with a
  warning to clean up manually.
- **Resolve a conflict**: align either the fragment (`chezmoi edit`) or the
  live settings, then `chezmoi apply` again. The base advances automatically
  once settings verifiably converge.

Exact semantics live in the docstring of `dot_claude/modify_settings.json`
(the engine; the snapshot script imports it), regression-tested in `tests/`.
