---
name: principle-reproducible-state
description: "Code & delivery. Apply when installing, patching, or working around a dependency, or editing generated/tool-owned files. Every change reproducible from committed state; mutate tool-owned files through the owning tool; escalate broken deps as pin → recorded patch mechanism → fork."
---

# Reproducible from committed state

**Every change must be reproducible from committed state.** Never mutate untracked state: installed packages (`site-packages`, `node_modules`), cloned dependency source, global machine config. When a dependency is broken, escalate in order: pin/upgrade the version → the ecosystem's recorded patch mechanism (`pnpm patch`, Cargo `[patch]`, `go.mod replace`, uv `[tool.uv.sources]` → fork) → fork and point the manifest at it. All three leave a replayable record; patching in place leaves none. Litmus test: a teammate reproduces this from a fresh clone with one command.

**Mutate tool-owned files through the tool, never by hand.** If a tool regenerates or validates a file, that file is a projection of tool state — change it via the owning command: `uv add`, `pnpm add`, `go get` + `go mod tidy`, `cargo add`, `mise use`, `alembic revision` (list is illustrative; the criterion decides). The command validates, resolves, and keeps manifest and lockfile in sync; a hand edit silently desyncs them. Hand-edit only sections the tool has no command for (e.g. `[tool.*]` blocks in pyproject.toml). Lockfiles are never hand-edited.
