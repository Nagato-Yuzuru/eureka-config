# Claude Code mods

Mods are Claude Code plugins whose behaviour is a TypeScript hooks module. This
folder holds ours, laid out like the [`mods/` folder of the Claude Code
repository](https://github.com/anthropics/claude-code/tree/main/mods), and the
local plain plugins that share their marketplace and checks.

| Mod                      | What it does                                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- |
| [`zle-words`](zle-words) | zsh's word widgets in the prompt box: `ctrl+w`, `alt+backspace`, `alt+b/f/d`, word boundaries from `$WORDCHARS` |

| Plugin                   | What it does                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| [`jbcontext`](jbcontext) | Air Context semantic code search, per repo: the `context-search` skill, and session hooks that keep the index fresh |

## jbcontext: enabled per repo

Indexing uploads code chunks to JetBrains, so the plugin is off by default
(`claude-managed.d/jbcontext.json`) and turned on per repository:

```sh
jbcontext index                                          # once, in the repo
claude plugin enable jbcontext@local-mods --scope local  # writes .claude/settings.local.json
```

Claude Code reads the main worktree's local settings from every linked
worktree, so one enable covers them all. Where the plugin is off, nothing of it
loads: no skill, no hook, no `jbcontext` run. `claude plugin disable
jbcontext@local-mods --scope local` turns it back off; `jbcontext remove-index`
deletes what was uploaded.

`tools/vendor/jbcontext.py` owns `.claude-plugin/plugin.json` and
`skills/context-search/`, and the jbcontext pin in `.chezmoidata/packages.toml`
that the CLI is installed at: the skill is the one released with that version,
and the plugin's version mirrors it. Edit none by hand; the vendor-sync PR moves
them together.

## How it is wired

```
claude/mods/                          repo-only (.chezmoiignore: claude/)
├── .claude-plugin/marketplace.json   the "local-mods" marketplace: lists every mod
├── package.json, .oxlintrc.json, …   toolchain shared by all mods
└── <mod>/                            a plain plugin
    ├── .claude-plugin/plugin.json
    ├── hooks/{hooks.json, register.ts}
    ├── tests/*.test.ts
    └── tsconfig.json                 extends the generated .claude-plugin/types/

dot_claude/symlink_mods.tmpl          ~/.claude/mods -> this folder
claude-managed.d/local-mods.json.tmpl registers ~/.claude/mods, enables each mod
```

Claude Code reads the mods in place through the link: there is no copy to keep
in sync, and an edit here is what the next session loads.

## Change a mod

1. Start a session with the mod loaded from its folder, so every save hot-reloads it:

   ```sh
   claude --plugin-dir ~/.claude/mods/<mod>
   ```

   This copy replaces the installed one for that session.

2. Edit `hooks/` and `tests/`.
3. Run the checks (below) until they pass.
4. Other sessions pick the change up on `/reload-plugins` or at their next start.

## Checks

```sh
cd ~/.local/share/chezmoi/claude/mods
pnpm install   # once
pnpm check     # gen-types, oxfmt, oxlint, tsc, validate, test
```

`.pre-commit-config.yaml` here makes this folder a prek workspace project, so a
commit runs `pnpm check` too, on top of the repo root's checks.

| Script           | What it does                                                                                                                                                                                                                                        |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm gen-types` | Has the installed Claude Code load each mod once (`claude -p --plugin-dir <mod> /exit`, no model call), which writes `<mod>/.claude-plugin/types/`. The types match the installed version and are not committed; rerun after upgrading Claude Code. |
| `pnpm fmt`       | Formats in place.                                                                                                                                                                                                                                   |
| `pnpm typecheck` | `tsc` per mod, against the generated types.                                                                                                                                                                                                         |
| `pnpm validate`  | `claude plugin validate` on the marketplace and each mod: manifests, and the static analysis Claude Code runs before loading a hooks module.                                                                                                        |
| `pnpm test`      | `claude plugin test` per mod: runs `tests/` against Claude Code's own host, no session or network.                                                                                                                                                  |

## Debug a mod that does nothing

1. `claude plugin validate ~/.claude/mods/<mod>`: the events it hooks and anything Claude Code would refuse.
2. In a session started with `--plugin-dir`, the transcript shows a dim line naming the mod when a hook throws, overruns its budget (`prompt.edit` has 50 ms), or the module fails to load.
3. `/plugin`: is `<mod>@local-mods` listed and enabled?
4. `claude --debug`: the log under `~/.claude/debug/` has a line for every refused or skipped hook.

## Add a mod

1. Create `<mod>/` with `.claude-plugin/plugin.json`, `hooks/hooks.json`, `hooks/register.ts`,
   `tests/`, and a `tsconfig.json` containing
   `{ "extends": "./.claude-plugin/types/tsconfig.json" }`.
2. Add it to `.claude-plugin/marketplace.json` and to `enabledPlugins` in
   `claude-managed.d/local-mods.json.tmpl` as `<mod>@local-mods`.
3. `pnpm check`, then `chezmoi apply ~/.claude/settings.json`.

## References

- [Create a mod](https://code.claude.com/docs/en/plugins/mods/create.md)
- [Test a mod](https://code.claude.com/docs/en/plugins/mods/test.md)
- [Troubleshoot a mod](https://code.claude.com/docs/en/plugins/mods/troubleshoot.md)
- [Mods reference](https://code.claude.com/docs/en/plugins/mods/reference.md)
