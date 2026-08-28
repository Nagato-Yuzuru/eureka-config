# eureka-config

The chezmoi source for my home directories: zsh (antidote, starship), tmux on
top of oh-my-tmux, mise, Claude Code settings, and a compiled inventory of
every tool a machine gets. Neovim lives in its own repo.

Two platforms exist, macOS with Homebrew and Fedora with dnf. A machine is
identified by `.chezmoi.os` plus `.chezmoi.arch` and nothing else, so the same
commit produces a Mac or a Fedora home directory depending on where it runs.

## Install

Both paths clone over SSH, so the machine needs an SSH identity GitHub
accepts. Accept github.com's host key when the first clone asks; that clone
happens before any script has run. The SSH clones that come later (the
externals) are covered by `run_once_before_trust-github-ssh.sh`, which pins
the keys from api.github.com/meta.

### macOS

Homebrew first:

```sh
brew install chezmoi
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
mise install   # its postinstall hook also generates the zsh completions
```

### Fedora

```sh
sudo dnf install -y chezmoi git zsh
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
```

Log out and back in for the login shell to become zsh, then `mise install`.

In a VM whose platform supplies its own kernel instead of Fedora's,
`kernel-devel` never matches the running kernel. The eBPF packages already
assume that: they take the BTF/CO-RE path (see the comment in
`packages.toml`).

## The loop

Edit, then `chezmoi apply`. That is the whole loop: chezmoi runs `make` before
every command that reads the source state, and make reruns the inventory
compiler when a data file, the compiler itself, or `uv.lock` moved. There is no
separate build step to remember.

```sh
make                     # rebuild .rendered/ and .schema/ by hand, if an input moved
uv run render schema     # only .schema/; from the types alone, works while a data file is broken
uv run render --check    # what prek runs: fails on a stale tree, writes nothing
uv run pytest            # everything, ~20 s
prek install             # the git hooks in .pre-commit-config.yaml
```

Changes reach other machines through git alone: commit, push, and
`chezmoi update` on the target pulls and applies.

What another machine gets is a file: `cat .rendered/darwin-amd64/mise-tools`.

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

Picking a backend: one global version, system package. Multiple versions,
mise. A Python CLI, uv. One exception is pinned: uv itself must stay a system
package on both platforms, because chezmoi's install script needs it before
mise has installed anything. That ordering is what lets the script run
`uv tool install` right after brew/dnf.

To add a tool, write the table and run `chezmoi apply`. The install script
reruns by itself, because this machine's rendered set changed. mise tools need
a `mise install` on top; that step is manual on purpose. Each layer has one
owner and one trigger: chezmoi owns system packages and uv tools, mise owns
its own. If no package manager ships the tool's completion, add a
`completions` recipe (next section).

To remove a tool, delete the table and run `chezmoi apply`. What happens to
the leftovers differs by backend, and only one of them cleans up after itself:

- A mise tool drops off PATH once the rendered `[tools]` section no longer
  lists it, but its versions stay on disk until `mise prune`.
- brew, dnf, and uv only ever install. A machine that already has the tool
  keeps it until a manual `brew uninstall` / `sudo dnf remove` /
  `uv tool uninstall`.
- Its generated completion disappears on the same apply: the recipe list
  changed, so `refresh-completions` reruns and rebuilds the directory from
  scratch.

### The fields

- `on` lists machine tags: `darwin`, `linux`, or `os/arch`. The set is open;
  a platform or device selector is one more variant, and existing lines do
  not change.
- `via` picks the backend and is the discriminator for the rest of the table.
  `system` (the default) means the platform's own package manager, brew on
  macOS and dnf on Fedora per `platforms.toml`, and allows both a `brew` and
  a `dnf` block, each read only where it applies. `mise` and `uv` allow
  exactly their own block.
- The blocks carry what only that backend understands: `brew = { name | cask }`,
  `dnf = { name }` plus at most one source (`copr` | `repofile`),
  `mise = { name | version | components }`, `uv = { name | version }`.
  `name` is the name on that backend when it differs from the key. `version`
  uses the backend's own syntax (`stable`, `lts`, `0.15.21`); declaring it and
  spelling it into a command are separate jobs, and the second belongs to each
  renderer. A block on the wrong `via`, or a `version` outside a block, is a
  type error, not an ignored field.
- rust-analyzer and rust-src ride on the rust toolchain as `components`.
  rustup's default profile ships neither, so without that line a new machine
  gets no language server.
- `overrides` is the only per-machine mechanism. Each entry whose `when` tag
  matches this machine is deep-merged over the line, in order: maps merge,
  lists and scalars replace. An entry that switches `via` starts the how
  afresh. One that sets a union block's deciding field (`cask`, `copr`,
  `repofile`) switches that block's variant instead of piling up. It may touch
  `via` and the blocks, never `on` or `completions`.

pnpm globals are not modelled. When the first one is needed, `via = "pnpm"`
takes the same shape as the others: one more package variant and renderer, and
a rendered mise task run by hand after `mise install` (pnpm is itself a mise
tool), `depends_post` on `refresh-completions`, no chezmoi hook.

### The types are the schema

`tools/inventory/model.py` (Pydantic) says exactly what a table may contain,
`inventory.py` resolves it per machine, `render.py` turns the resolved list
into text. Bad data fails `render` with a message naming the entry
(`packages.rust.mise.components: must be a non-empty list`).

The same types also produce `.schema/<file>.json`, and the `#:schema` line at
the top of each data file points there, so a TOML language server (taplo,
tombi) completes and checks the file against the same constraints `render`
enforces. `deno run npm:@taplo/cli lint .chezmoidata/*.toml` does the same
from a shell.

`.rendered/` is build output and gitignored; the make hook keeps it current.
`.schema/` is committed, because the editor reads it before anything has had a
chance to run. Templates pull in the artifact for `<os>-<arch>` through
`.chezmoitemplates/artifact`, which recomputes the hash of the three data
files, compares it to `.rendered/STAMP`, and fails on a mismatch or on a
machine `machines.toml` does not declare. A stale or foreign tree can never be
applied; the failure names the cause, and a plain `make` rebuilds it.

## Zsh completions

Completion definitions come from four layers, none of them hand-written here:
zsh's own compsys (`_git _tmux _jq`), what brew formulae and Fedora rpms
install alongside the tool (`_gh _bat _fd _rg _uv`), what the tool generates
about itself (`kubectl completion zsh`, `COMPLETE=zsh prek`), and
zsh-users/zsh-completions (`_golang _node _tsc _cmake`). The third layer is
the only one nobody installs for us, and `packages.toml` carries its recipes
in `completions`. A tool that is not in `packages.toml` has no completion
here, and should not need one.

- `mise run refresh-completions` (rendered per machine, so it only knows this
  machine's recipes) rebuilds the generated dir from scratch. The dir is
  `zsh.generatedCompletions` in `.chezmoidata/zsh.toml`, under
  `$XDG_CACHE_HOME`. The task skips commands not installed, runs each recipe
  with stdin closed, checks the output starts with `#compdef`, then swaps the
  directory in and drops `~/.zcompdump`.
- Two things make the generated set stale, and each has its own trigger. A
  tool binary changed: mise's `postinstall` hook runs the task after every
  `mise install` / `mise up`. The recipe list changed: chezmoi's
  `run_onchange_after_refresh-completions` reruns whenever this machine's
  rendered recipes differ (the list is rendered into the script for that
  alone). On a new machine that script runs before `mise install` has, sees an
  incomplete toolset and defers to the hook. Nothing runs at shell startup.
- The generated dir is a cache. A wiped `$XDG_CACHE_HOME` is repaired by
  running `mise run refresh-completions` by hand.
- `.zshrc` puts that directory **last** on fpath. compinit takes the first
  `_<cmd>` it finds, so a completion the package manager ships always beats a
  generated one. That is what makes a recipe safe to declare on any tool:
  where brew or dnf already ship the file, ours is a shadowed no-op; where
  they don't (Fedora's mise and starship rpms), it fills the gap. Nothing in
  `packages.toml` records who ships what, and `completions` cannot be
  overridden per machine, because it never needs to be.
- `clap_stub <cmd> '<generator>'` in a recipe wraps a clap dynamic completer
  (`COMPLETE=zsh prek`) whose script must be sourced, so it works from a file.
- `mise run audit-completions` reports, for this machine, which packages ship
  their own completion, who completes every command mise puts on PATH and
  every entry point of the uv tools, and whether each recipe is what actually
  completes here or is shadowed. Run it before adding or doubting a recipe.
- Not files: terraform and tofu only speak bash's `complete -C` protocol and
  are wired through bashcompinit in `.zshrc`; `sg` borrows `_ast-grep` there
  too.
- `~/.local/share/zsh/site-functions` stays first on fpath for anything
  dropped by hand; the generator never touches it.

## Machines and platforms

- Another os/arch of an existing platform is one line in
  `.chezmoidata/machines.toml`.
- A new platform (a new OS and its package manager) is an entry in
  `platforms.toml` plus whatever its backend needs in `model.py` and
  `render.py`.
- A specific device is not expressible yet. The artifact template picks by
  `<os>-<arch>` alone, so two devices sharing that pair would need a
  machine-level Selector variant in `model.py` and a way for the template to
  know which machine it is on. The seam exists; the feature does not.

## Layout

| Path                                 | What                                                                                              |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `.chezmoidata/packages.toml`         | every tool, one table each, whatever installs it                                                  |
| `.chezmoidata/platforms.toml`        | kinds of machine: OS and the system package manager that comes with it                            |
| `.chezmoidata/machines.toml`         | the render targets, `<os>-<arch>`                                                                 |
| `.chezmoidata/xdg.toml`              | XDG base dirs; `.zshenv` exports them, externals and symlinks derive their paths from them        |
| `.chezmoidata/zsh.toml`              | the generated-completions dir, declared once for `.zshrc` and both mise tasks                     |
| `tools/inventory/`                   | the inventory compiler: types, resolution, renderers; `uv run render` writes `.rendered/`         |
| `.rendered/<machine>/`               | the compiled artifacts chezmoi includes (Brewfile lines, mise tools, recipes ...), gitignored     |
| `.schema/`                           | JSON Schema per data file, from the same types; each `.toml` names its own, committed             |
| `Makefile`                           | rebuilds the two generated trees when an input moved; chezmoi's hook runs it                      |
| `.chezmoitemplates/artifact`         | includes this machine's artifact; refuses a stale tree or an undeclared machine                   |
| `.chezmoiexternal.toml.tmpl`         | oh-my-tmux and (linux) antidote as git clones under `$XDG_DATA_HOME`                              |
| `.chezmoiignore`                     | what a non-Mac never gets: Brewfile, ghostty, wezterm, 1Password                                  |
| `run_once_before_trust-github-ssh.sh`| pins github.com's host keys from api.github.com/meta, so the externals can clone non-interactively |
| `run_onchange_before_install-*.sh`   | installs the system packages (brew bundle / dnf) and uv tools whenever this machine's set changes |
| `run_once_after_setup-linux.sh`      | Fedora bootstrap: login shell, git include and identity                                           |
| `run_onchange_after_install-yazi-*`  | `ya pkg install` whenever yazi's `package.toml` (a rev+hash lockfile) changes                     |
| `run_onchange_after_refresh-*.sh`    | regenerates the zsh completions whenever this machine's recipe list changes                       |
| `dot_config/mise/tasks/`             | mise file tasks rendered per machine: `refresh-completions`, `audit-completions`                  |
| `dot_claude/`                        | Claude Code: CLAUDE.md, hooks, skills, and the managed-settings fragments (next section)          |
| `tests/`                             | pytest for the inventory and the settings merge engine; never deployed                            |

## Claude Code managed settings: the fragment protocol

`~/.claude/settings.json` is co-owned: chezmoi manages a declared subset of it,
everything else belongs to Claude Code and the user. The subset is declared as
per-feature fragments and merged in, never templated wholesale.

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

- `$merge` declares `{"dotted.path": strategy}` for the paths this file owns
  (`*` matches exactly one segment). Ownership semantics are declared, never
  inferred from data shape:

  | strategy | meaning                                                                                                                    |
  | -------- | -------------------------------------------------------------------------------------------------------------------------- |
  | `exact`  | the value is one atomic leaf (deep equality). Default for every non-object; declare on an object to own the whole subtree. |
  | `ensure` | list as a three-way element set: foreign elements kept, our retired elements removed, missing ones appended.               |
  | `keyed`  | list entries claimed by hook-command basename: ours replaced/retired, foreign entries pass through.                        |

  Objects without a declaration recurse per key.

- `$legacyHooks` lists retired hook basenames to purge (union across
  fragments).

### Invariants

- Declarative in all three directions. Add or change a fragment and settings
  converge; delete a fragment file (or a path in one) and the slice is retired
  from settings on the same apply, as long as it still equals the base.
- Fail-safe against the outside world. A foreign edit to a value we own is a
  conflict: reported on stderr, `chezmoi apply` exits 1, settings untouched. A
  foreign edit to a value we retired is kept with a warning, because ownership
  was withdrawn. Nothing foreign is ever silently overwritten; without a base
  the engine errs toward conflicts.
- Fragments must be self-consistent. Cross-fragment value collisions, strategy
  contradictions (overlapping or nested patterns), duplicate claim keys and
  ensure elements, and unknown `$` keys are compose errors, all reported at
  once, apply refused. They surface on `chezmoi apply`: the filter composes
  the deployed fragments, so a contradiction in freshly edited sources shows
  up when apply deploys them and then refuses the settings target.
- Formatting-stable. A no-op merge passes the original bytes through, so
  `chezmoi diff` never shows pure formatting churn.

### Operations

- Add a feature: new `<feature>.json.tmpl` with its own `$merge` declarations.
  Same-path declarations across files must agree.
- Remove a feature: delete its source file. Its hooks and list elements vanish
  on the next apply; anything the user modified meanwhile is left with a
  warning to clean up manually.
- Scope a feature to one kind of machine: split it into `<feature>.json` and
  `<feature>-<os>.json`, then exclude the second one in `.chezmoiignore`. A file
  that never deploys leaves its keys unmanaged on that machine. Moving a key out
  of the shared file counts as a removal everywhere else, so it retires on the
  next apply.
- Resolve a conflict: align either the fragment (`chezmoi edit`) or the live
  settings, then `chezmoi apply` again. The base advances by itself once
  settings verifiably converge.

Exact semantics live in the docstring of `dot_claude/modify_settings.json`
(the engine; the snapshot script imports it), regression-tested in `tests/`.

### Plugin declarations

`plugins.json` holds the plugins and marketplaces that belong on every machine.
`plugins-darwin.json` holds the ones that only make sense on macOS, today just
`playwright`. Neither file declares a `$merge` strategy, so the default applies
and every plugin id is owned as its own leaf. A plugin you install by hand on
one machine is a foreign key that chezmoi never touches.

Three things are left out on purpose.

`~/.claude/local-plugins/` (the `ts7-lsp` and `ty-lsp` plugins) is not managed
here, so the `local` marketplace and the two `@local` entries stay out of the
fragments as well. A fresh machine has no such directory, and pointing at one
would only produce two permanent rows in the `/plugin` Errors tab.

The plugins currently set to `false` are not declared either. "Installed and
switched off" is a state the `/plugin` UI already holds, and copying it into the
source only creates a second place to keep true.

There is no install step, and that is the part that catches people out. Claude
Code adds the marketplaces from `extraKnownMarketplaces` by itself once the
folder is trusted, but it will not install a plugin just because settings enable
it. On a new machine the declared plugins report as not installed until you run
the `claude plugin install` line Claude Code prints. A script that did this for
you would have to track install scope, marketplace refresh and version pinning
to stay idempotent, which is a lot of moving parts for a step you take once per
machine.

Day to day this is one line. Add a plugin by adding its id to `plugins.json`, or
to `plugins-darwin.json` if it should stay on the Mac. Delete the line and the
key disappears from `settings.json` on the next apply, as long as nobody changed
it meanwhile. That withdraws the declaration. It does not uninstall anything.
