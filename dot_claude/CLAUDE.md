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

## Engineering Principles

**Error handling**: Prefer loud failure over silent suppression. Do not add fallbacks, default values, or catch-and-continue logic for internal code paths. At system boundaries (user input, external APIs), handle errors explicitly — everywhere else, let them propagate so they surface early.

**Tests**: Tests must verify actual runtime behavior and must be capable of failing. Do not write tests that always pass regardless of behavior — e.g., asserting a function exists, checking its type signature, or testing constants.

**No duplication**: If something is defined in one place, reference or import it — don't copy it. Duplication creates divergence.

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

**Default to `haiku` for any subagent that does not require reasoning or code generation.** If you are unsure, ask yourself: "Does this task require judgment, or just retrieval and aggregation?" If the answer is retrieval/aggregation, use `haiku`.

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

## API Design

- When designing or modifying APIs, you MUST fetch and read the relevant AIP rules from https://google.aip.dev before proceeding. NEVER assume you know what an AIP rule says from memory — always verify against the actual documentation.
