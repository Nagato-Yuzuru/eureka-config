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
| `.chezmoidata/packages.yaml`        | every tool, one line each, whatever installs it (see below)                                |
| `.chezmoidata/xdg.yaml`             | XDG base dirs; `.zshenv` exports them, externals and symlinks derive their paths from them |
| `.chezmoitemplates/`                | the package renderers and validator                                                        |
| `.chezmoiexternal.toml.tmpl`        | oh-my-tmux and (linux) antidote as git clones under `$XDG_DATA_HOME`                       |
| `.chezmoiignore`                    | what a non-Mac never gets: Brewfile, ghostty, wezterm, 1Password                           |
| `run_onchange_before_install-*.sh`  | installs the system packages (brew bundle / dnf) whenever this machine's set changes       |
| `run_once_after_setup-linux.sh`     | Fedora bootstrap: login shell, git include and identity                                    |
| `run_onchange_after_install-yazi-*` | `ya pkg install` whenever yazi's `package.toml` (a rev+hash lockfile) changes              |
| `dot_claude/`                       | Claude Code: CLAUDE.md, hooks, skills, and the managed-settings fragments (last section)    |
| `tests/`                            | pytest for the package model and the settings merge engine; never deployed                 |

## Packages

`packages.yaml` is the inventory. One line per tool; the fields say where it
goes and how it gets there.

```yaml
fd:      { on: [darwin, linux], dnf: fd-find }
ghostty: { on: [darwin], cask: ghostty }
rust:    { on: [darwin, linux], via: mise, version: stable }
pnpm:    { on: [darwin, linux], via: mise, overrides: [{ $when: darwin/amd64, mise: "npm:pnpm" }] }
```

- `on` is a list of machine tags: `darwin`, `linux`, or `os/arch`.
- `via` picks the backend. `system` (the default) means brew on a Mac and dnf
  on Fedora; `mise` puts the tool in mise's `[tools]`. The rule I use: one
  global version, system package; multiple versions, mise. uv is a system
  package on both, it never needs more than one version and chezmoi's own
  scripts need it before mise has installed anything.
- `brew` / `dnf` / `mise` override the name on that backend when it differs
  from the key. `copr` and `repofile` add the Fedora repo the package needs.
- `overrides` is the only per-machine mechanism. Each entry whose `$when` tag
  matches this machine is merged over the line, in order. Any field can differ
  per machine that way; today only pnpm does.

From that one file chezmoi renders `~/.Brewfile`, the `[tools]` block of
`~/.config/mise/config.toml`, and the dnf half of the install script. Bad data
fails the render with a message naming the entry. `tests/golden/<machine>/`
holds the rendered lists per machine, so a diff there shows exactly which
machine an edit touched.

chezmoi installs the system packages but never runs `mise install`; that is a
manual step and stays one.

## New machine

Mac (Homebrew and 1Password's SSH agent already set up):

```sh
brew install chezmoi
chezmoi init --apply git@github.com:Nagato-Yuzuru/eureka-config.git
mise install
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

The VM runs OrbStack's kernel, not Fedora's, so `kernel-devel` never matches and
the eBPF section of `packages.yaml` takes the BTF/CO-RE path throughout.

## Development

```sh
uv run pytest                                          # everything, ~8 s
UPDATE_GOLDEN=1 uv run pytest tests/test_packages.py   # after an intentional packages.yaml change
prek install                                           # the git hooks in .pre-commit-config.yaml
```

To see what another machine would get without being on it:

```sh
printf '{{ includeTemplate "mise-tools" . }}' |
  chezmoi execute-template --override-data '{"chezmoi": {"os": "darwin", "arch": "amd64"}}'
```

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
