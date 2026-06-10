# Global Preferences

## JavaScript / TypeScript — Package Manager

- Always use `pnpm` instead of `npm` for all package operations.
- Never run `npm install`, `npm ci`, `npm run`, or `npx`.

| Intent               | Command                                  |
| -------------------- | ---------------------------------------- |
| Install dependencies | `pnpm install`                           |
| Add a package        | `pnpm add <pkg>`                         |
| Run a script         | `pnpm run <script>` or `pnpm <script>`   |
| One-off execution    | `pnpm dlx <pkg>` or `deno run npm:<pkg>` |

Prefer `deno run npm:<pkg>` over `pnpm dlx` for non-interactive, minimal-access tools (formatters, type checkers, codegen). Use `pnpm dlx` for interactive CLIs or tools needing filesystem/network access.

## Python — Toolchain

- **Never** invoke the system Python or modify system-level packages.
  - Forbidden: `python`, `python3`, `pip`, `pip3`, `python -m pip`, `sudo pip`, `virtualenv`, `poetry`.
  - This applies even inside a venv — always go through `uv`.
- Always use `uv` for environment and dependency management.
- For single-file scripts with dependencies, prefer PEP 723 inline metadata (`uv run file.py`) over creating a full project.
- Always use `ty` for static type checking instead of `mypy` or `pyright`.

| Intent                         | Command              |
| ------------------------------ | -------------------- |
| Create / sync environment      | `uv sync`            |
| Add a package                  | `uv add <pkg>`       |
| Add a dev dependency           | `uv add --dev <pkg>` |
| Run a script                   | `uv run <script>`    |
| One-off tool execution         | `uvx <tool>`         |
| Run Python directly            | `uv run python`      |
| Run a PEP 723 inline-meta file | `uv run <file.py>`   |
| Type-check project             | `ty check`           |
| Type-check a file              | `ty check <path>`    |
| Audit deps (missing / unused)  | `uvx deptry .`       |

## Python — Code Style

- **Imports go at module top level.** Do not place `import` statements inside
  functions or methods. The only exceptions are a genuine, documented reason:
  breaking an actual circular import, or guarding a heavy/optional dependency
  that must stay lazy. "It's only used in one function" is NOT a reason — a
  function-local import hides the dependency, defeats static analysis, and
  re-runs the import machinery on every call. If you add a function-local
  import, add a comment stating which of the two allowed reasons applies.

- **Declare every dependency in `pyproject.toml`.** Any third-party package you
  `import` must be added via `uv add <pkg>` (or `uv add --dev <pkg>` for tooling)
  so it lands in `pyproject.toml` and the lockfile. Never rely on a transitively-
  or ambient-installed package. If you introduce an import, update
  `pyproject.toml` in the same change — code that imports an undeclared package
  is incomplete. `deptry` (`uvx deptry .`) flags missing and unused
  dependencies — run it to verify.

- **Be liberal in what you accept, strict in what you emit.** Type a parameter
  with the narrowest interface that actually works — `Iterable[SomeInterface]`,
  not `list[SomeConcrete]` — and return concrete, specific types. Never annotate
  with a bare, unparameterized `dict` / `list` / `tuple`: they carry no
  information and defeat the type checker. For structured dict-shaped data, use a
  `TypedDict`, or better a `@dataclass(slots=True)` when the shape is owned by
  your code.

- **No `print` for application output (ruff T20).** Use `logging` (or
  `structlog`) so output has levels, structure, and routing. `print` is
  acceptable only in genuinely throwaway, one-off scripts.

## Go — Code Style

- **Never ignore a returned `error` without an extremely strong, documented
  reason.** When you do intentionally ignore one, assign it to `_` and add a
  comment stating why it is safe to drop — e.g.
  `_ = w.Close() // best-effort cleanup; error not actionable here`. A dropped
  error with no `_` and no comment is a bug. (This is the Go-specific form of the
  loud-failure principle below.)

- **Inspect errors with `errors.Is` / `errors.As`, never by string-matching.**
  Do not write `if err.Error() == "..."` or `strings.Contains(err.Error(), ...)`
  to branch on an error — the message is for humans and can change. Use
  `errors.Is(err, ErrSentinel)` to test for a sentinel and `errors.As(err,
&target)` to extract a typed error. Wrap with `fmt.Errorf("...: %w", err)` to
  preserve the chain so `Is`/`As` keep working up the stack.

- **Every function that does I/O, network/fetch, or starts concurrency takes a
  `context.Context`** as its first parameter (conventionally `ctx`) and
  propagates it to the calls it makes. Do not store a context in a struct field,
  and do not mint a fresh `context.Background()` deep in the call tree when a
  caller's context is available.

- **Goroutines must have a managed lifecycle.** Every goroutine you start must be
  bounded by `ctx` cancellation, an `errgroup.Group`, a `sync.WaitGroup`, or
  channel signaling — something that guarantees it terminates. No
  fire-and-forget goroutines that can outlive their caller and leak.

## Engineering Principles

_These principles are language-agnostic — apply them across all languages, not only Python._

**Error handling**: Prefer loud failure over silent suppression. Do not add fallbacks, default values, or catch-and-continue logic for internal code paths. At system boundaries (user input, external APIs), handle errors explicitly — everywhere else, let them propagate so they surface early. Never silently swallow an error anywhere: discarding or ignoring one requires a specific, documented reason and an inline comment marking it intentional (in Go, the ignored value must be assigned to `_` with that comment).

**Tests**: Tests must verify actual runtime behavior and must be capable of failing. Do not write tests that always pass regardless of behavior — e.g., asserting a function exists, checking its type signature, or testing constants.

**No duplication**: If something is defined in one place, reference or import it — don't copy it. Duplication creates divergence.

**Precise types over escape hatches**: Avoid dynamic escape-hatch types — Python `Any`, Go `any` / `interface{}`, TypeScript `unknown` / `any` — unless there is a specific, documented reason (genuinely dynamic data whose shape cannot be known, or an untyped third-party boundary). They disable the type checker for everything they touch. Prefer real types: concrete classes, generics, `Protocol` / interfaces, unions, `TypedDict`. When an escape hatch is truly necessary, add a comment explaining why a real type is not feasible.

## Code Search — ast-grep vs. ripgrep

- **Default to the ripgrep-backed `Grep` tool** for text searches: a string, identifier, import, log line, or "where does this name appear".
- **Reach for `ast-grep` (`sg`) when the match depends on syntax, not text:**
  - Call sites of a function (`foo($$$)`) — without hitting its definition, comments, or same-named strings.
  - Filtering by node type (only type annotations, only decorators, a specific `try/catch` shape).
  - Multi-line structural shapes that defeat regex.
  - Structural rewrites / codemods (`sg --rewrite`).
- **An empty `ast-grep` result never proves absence.** A mistyped pattern or unsupported language silently yields zero matches. On a surprising miss, fall back to ripgrep and say so — never report "not found" on an ast-grep miss alone.
- Use ripgrep when the language isn't in ast-grep's parsers.

## Subagent & Workflow Model Selection

**This rule is MANDATORY. You MUST follow it every single time you spawn a subagent, without exception.** "Spawning a subagent" covers BOTH mechanisms:

1. **The Agent tool** — set its `model` parameter.
2. **The Workflow tool's `agent()` calls** — set `opts.model` on every call.

Select the model based on task type:

| Task type                                              | Required model |
| ------------------------------------------------------ | -------------- |
| Web fetching, summarization, document retrieval        | `haiku`        |
| File search, grep/glob aggregation, log scanning       | `haiku`        |
| Code reasoning, architecture analysis, code generation | `sonnet`       |
| Complex multi-file planning or refactoring             | `opus`         |

When a task straddles the `sonnet`/`opus` boundary (code generation that also spans files), tie-break to `opus` if it crosses multiple files or must preserve cross-file consistency; otherwise stay on `sonnet`.

**Default to `haiku` for any subagent that does not require reasoning or code generation.** If you are unsure, ask yourself: "Does this task require judgment, or just retrieval and aggregation?" If the answer is retrieval/aggregation, use `haiku`.

**`fable` is not a default for any row above — it is an explicit, deliberate opt-in.** Fable 5 sits above Opus but costs ~2× Opus ($10/$50 vs $5/$25 per MTok). Use it only when you have specifically judged that a subagent needs reasoning beyond Opus's ceiling (e.g. an exceptionally hard multi-file refactor where correctness justifies the 2× cost). It is on-demand escalation, never the standing choice for a task type. When in doubt, `opus` is the top of the routine table — do not reach past it.

Never spawn a subagent without explicitly setting its model — the Agent tool's `model` parameter or `agent()`'s `opts.model`. Inheriting the parent model for lightweight tasks is **wasteful and forbidden**.

**Workflow caveat — this is the trap to avoid.** Workflow's `agent()` defaults to _inheriting the main-loop model_ (often `opus`), which silently bypasses this table and makes every workflow agent run as Opus. The Workflow tool's own guidance ("when unsure, omit `model`") is OVERRIDDEN by this rule. In a workflow script, encode the table once and pass `opts.model` on every `agent()`:

```js
const M = {
  fetch: "haiku",
  search: "haiku",
  scan: "haiku",
  reason: "sonnet",
  codegen: "sonnet",
  plan: "opus",
};
await agent(`Fetch and summarize ${url}`, { model: M.fetch });
```

Mirror the choice in `meta.phases[].model` for the progress UI if you like, but `opts.model` is what actually selects the model.

## Interface & API Design

- AIP (https://google.aip.dev) is the source of truth for API design. Distinguish two layers:
  - Its **principles / philosophy** — consistency, predictability, standard verbs, resource naming, pagination thinking — generalize broadly. Let them inform REST, gRPC, CLI, and program-internal interfaces alike.
  - Its **concrete rules** — HTTP mapping, field masks, long-running operations, the `:customVerb` syntax, standard methods — are written for resource-oriented network APIs, primarily gRPC/protobuf and Google-style REST. Apply them there; do not force RPC/streaming/GraphQL/CLI/internal interfaces into them.
- Whenever a specific AIP rule bears on what you are designing or modifying, you MUST fetch and read that rule before proceeding. NEVER assume you know what an AIP rule says from memory — always verify against the actual documentation.
