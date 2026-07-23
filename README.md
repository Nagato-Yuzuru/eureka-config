# eureka-config

dotfiles, Workflow and toolchain

## Claude Code managed settings — the fragment protocol

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

- `$merge` — `{"dotted.path": strategy}` declarations for the paths this file
  owns (`*` matches exactly one segment). Ownership semantics are **declared,
  never inferred from data shape**:

  | strategy | meaning                                                                                                                    |
  | -------- | -------------------------------------------------------------------------------------------------------------------------- |
  | `exact`  | the value is one atomic leaf (deep equality). Default for every non-object; declare on an object to own the whole subtree. |
  | `ensure` | list as a three-way element set: foreign elements kept, our retired elements removed, missing ones appended.               |
  | `keyed`  | list entries claimed by hook-command basename: ours replaced/retired, foreign entries pass through.                        |

  Objects without a declaration recurse per key.

- `$legacyHooks` — retired hook basenames to purge (union across fragments).

### Invariants

- **Declarative in all three directions.** Add/change a fragment → converges;
  delete a fragment file (or a path in one) → the slice is _retired_ from
  settings on the same apply, as long as it still equals the base.
- **Fail-safe against the outside world.** A foreign edit to a value we own is
  a conflict: reported on stderr, `chezmoi apply` exits 1, settings untouched.
  A foreign edit to a value we _retired_ is kept (warning only) — ownership
  was withdrawn, so it never blocks. Nothing foreign is ever silently
  overwritten; without a base the engine errs toward conflicts.
- **Fragments must be self-consistent.** Cross-fragment value collisions,
  strategy contradictions (overlapping or nested patterns), duplicate claim
  keys and ensure elements, and unknown `$` keys are compose errors — all
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
