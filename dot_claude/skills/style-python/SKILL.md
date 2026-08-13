---
name: style-python
description: "Apply when writing or reviewing Python. Imports at module top, deps declared via uv in the same change, narrowest interface in / concrete types out, no print in app code, native idioms over JS reflexes. Env/package commands live in CLAUDE.md (uv table)."
disable-model-invocation: true
---

# Python style

- **Imports at module top level.** Only two exceptions, each requiring a comment naming which applies: breaking a real circular import, or keeping a heavy/optional dependency lazy. "Only used in one function" is not a reason — a local import hides the dependency and defeats static analysis.
- **Every third-party import is declared via `uv add` in the same change** — code importing an undeclared package is incomplete. Audit with `uv run --with deptry deptry .` (never via `uvx`: an isolated env mis-guesses module names, e.g. `pyyaml`→`yaml`). In a monorepo run it per package; whitelist runtime-only backends via `[tool.deptry.per_rule_ignores]`.
- **Accept the narrowest interface that works, return concrete types** — `Iterable[SomeInterface]` in, specific types out. Never bare `dict`/`list`/`tuple`; dict-shaped data gets a `TypedDict`, or `@dataclass(slots=True)` when you own the shape.
- **No `print` for application output** (ruff T20) — use `logging`/`structlog`. `print` only in throwaway scripts.
- **When scaffolding a project, enable at least ruff families `PTH`, `C4`, `SIM`, `T20`** — mechanize idiom instead of relying on prose (see **principle-mechanization-ladder**).
- Static type checking: `ty check`.

**Write Python, not JavaScript.** Litmus test: if the code would translate to JavaScript nearly line-for-line, you are writing the intersection of all languages — rewrite with native constructs:

| JS reflex                                  | Native idiom                                      |
| ------------------------------------------ | ------------------------------------------------- |
| dict + `.get()` chains for structured data | `dataclass` / `TypedDict`; let `KeyError` surface |
| manual accumulator loops                   | comprehensions / generators / itertools           |
| try/finally cleanup; string-built paths    | context managers; `pathlib`                       |
| defensive `if x is None` / `x or default`  | EAFP — let exceptions propagate                   |
| async by default                           | sync unless concurrency is real                   |

Environment and package-management commands (uv table, PEP 723 scripts) live in CLAUDE.md.
