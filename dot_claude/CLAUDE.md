# Global Preferences

## File Access Boundary — HARD RULE

**Default-ask whitelist: file tools may touch the workspace and a short list of known-good paths; everything else prompts me first, for reads and writes alike.** There is deliberately no "sensitive paths" list — protection comes from the closed default, not from enumeration.

Allowed without asking:

- the workspace: project directory plus `permissions.additionalDirectories` (`/add-dir`)
- `/private/tmp/**` — the OS's shared scratch; clone-and-inspect work goes here or in the session scratchpad
- auto-memory for the current project (`~/.claude/projects/<current slug>/memory/`)
- read-only, Claude Code's own layout: `~/.claude/projects/<current slug>/**`, `~/.claude/{plugins,skills,agents,commands,rules,output-styles}/**`, `~/.claude/CLAUDE.md`, `~/.claude/keybindings.json`

Everything else asks. Authorization has three lifetimes:

- **session** — approving the permission prompt auto-records that exact path for the rest of the session (a pending→promote handshake; nothing is recorded in `bypassPermissions`).
- **TTL / permanent** — a durable grant in `~/.claude/hooks/path-gate-grants.json`. Issued ONLY via the canonical command `~/.claude/hooks/path-gate.sh grant <path> <r|w|rw> [--global | --project DIR] [--ttl 7d]`. You (Claude) may run it when I ask for durable access — the native `permissions.ask` rules on `grant`/`revoke` always trigger a confirmation prompt, in every permission mode. Use exactly that invocation: never through copies, renames, variables, or other spellings, and never `revoke` or edit stores on your own initiative.
- Read-only verbs `list` and `check <path> <r|w>` run freely.

Tool-owned stores — never hand-edit through any tool or shell trick: `~/.claude/hooks/path-gate-grants.json` and `~/.claude/hooks/path-gate-sessions/`. File-tool writes to them are hard-denied by the gate; do not route around via Bash.

Enforcement: PreToolUse hook `~/.claude/hooks/path-gate.sh` (Read/Grep/Glob need `r`; Edit/Write/NotebookEdit need `w`; decisions are made on resolved real paths, so symlinks cannot relocate the boundary), a PostToolUse recorder, native `permissions.ask` rules gating `path-gate.sh grant`/`revoke`, and the auto-mode classifier soft-blocking out-of-workspace Bash writes. Being stopped by these gates is intended behavior — never disable a hook or use `dangerouslyDisableSandbox` to get past them. If a task needs a path outside the whitelist, either let the prompt do its job or stop and propose a grant, naming the path and mode (r/w) you need.

## Shell / Bash Tool — Active Aliases

The Bash tool runs through my interactive zsh config (shell snapshot), so these aliases/functions are **live — it is not a vanilla shell**. Bypass any with `command <cmd>` (works for aliases and functions; `\cmd` escapes aliases only).

| Typed  | Actually runs                 | Note                                                                                                                                  |
| ------ | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `rm`   | `rm -i`                       | no TTY → silently **skips** the delete (does not hang); use `command rm` when you mean it                                             |
| `mv`   | `mv -i`                       | no TTY → silently **skips** overwrite when dest exists                                                                                |
| `sed`  | `gsed`                        | GNU syntax — do NOT use BSD `sed -i ''`                                                                                               |
| `date` | `gdate`                       | GNU flags (`-d`, `+%s%N`), not BSD `-v`                                                                                               |
| `grep` | ripgrep (Claude Code wrapper) | gitignore/VCS-aware, skips binaries — **not POSIX grep** (won't search ignored/.git files), not colored; `command grep` for real grep |

(`ls`/`fd` are aliased to `--color=auto` but that is a no-op here — color is auto-suppressed when output isn't a TTY.)

## JavaScript / TypeScript

Package manager is `pnpm` — never `npm`/`npx`. (A PreToolUse hook also blocks them; its error message carries this map.)

| Intent               | Command                                  |
| -------------------- | ---------------------------------------- |
| Install dependencies | `pnpm install`                           |
| Add a package        | `pnpm add <pkg>`                         |
| Run a script         | `pnpm run <script>` or `pnpm <script>`   |
| One-off execution    | `pnpm dlx <pkg>` or `deno run npm:<pkg>` |

Prefer `deno run npm:<pkg>` for non-interactive, minimal-access tools (formatters, type checkers, codegen); `pnpm dlx` for interactive CLIs or tools needing filesystem/network access.

## Python

- **Never invoke the system Python or modify system-level packages** — `python(3)`, `pip(3)`, `python -m pip`, `sudo pip`, `virtualenv`, `poetry` are all forbidden, even inside a venv. Everything goes through `uv`.
- Static type checking: `ty`.
- Single-file scripts with deps: PEP 723 inline metadata + `uv run file.py`, not a full project. Manage the metadata through uv — `uv init --script` to create, `uv add --script` to add deps — not by hand-editing the inline block (it is a tool-owned file, per the rule below).

| Intent                         | Command                           |
| ------------------------------ | --------------------------------- |
| Create / sync environment      | `uv sync`                         |
| Add a package                  | `uv add <pkg>`                    |
| Add a dev dependency           | `uv add --dev <pkg>`              |
| Run a script                   | `uv run <script>`                 |
| One-off tool execution         | `uvx <tool>`                      |
| Run Python directly            | `uv run python`                   |
| Create a PEP 723 script        | `uv init --script <file.py>`      |
| Add a dep to a PEP 723 script  | `uv add --script <file.py> <pkg>` |
| Run a PEP 723 inline-meta file | `uv run <file.py>`                |
| Type-check project / file      | `ty check [path]`                 |
| Audit deps (missing / unused)  | `uv run --with deptry deptry .`   |

### Python style

- **Imports at module top level.** Only two exceptions, each requiring a comment naming which applies: breaking a real circular import, or keeping a heavy/optional dependency lazy. "Only used in one function" is not a reason — a local import hides the dependency and defeats static analysis.
- **Every third-party import is declared via `uv add` in the same change** — code importing an undeclared package is incomplete. Audit with `uv run --with deptry deptry .` (never via `uvx`: an isolated env mis-guesses module names, e.g. `pyyaml`→`yaml`). In a monorepo run it per package; whitelist runtime-only backends via `[tool.deptry.per_rule_ignores]`.
- **Accept the narrowest interface that works, return concrete types** — `Iterable[SomeInterface]` in, specific types out. Never bare `dict`/`list`/`tuple`; dict-shaped data gets a `TypedDict`, or `@dataclass(slots=True)` when you own the shape.
- **No `print` for application output** (ruff T20) — use `logging`/`structlog`. `print` only in throwaway scripts.
- **When scaffolding a project, enable at least ruff families `PTH`, `C4`, `SIM`, `T20`** — mechanize idiom instead of relying on prose (see Mechanization ladder below).

## Go style

- **Never silently ignore a returned `error`.** Intentional drops are `_ = w.Close() // why it's safe here`. No `_`, no comment → it's a bug.
- **Inspect errors with `errors.Is`/`errors.As`, never string-matching** — the message is for humans and can change. Wrap with `fmt.Errorf("...: %w", err)` to keep the chain.
- **Every function doing I/O or starting concurrency takes `ctx context.Context` first** and propagates it. No context in struct fields; no fresh `context.Background()` deep in the tree when a caller's ctx exists.
- **Every goroutine has a managed lifecycle** — bounded by ctx cancellation, `errgroup`, `WaitGroup`, or channel signaling. No fire-and-forget.

## Engineering Principles

_Language-agnostic._

**Mechanization ladder — prose is the enforcement of last resort.** To encode a convention, prefer (strongest first): types > linter/formatter > tests > hooks/CI > instructions in this file. When you repeatedly follow (or violate) the same prose rule, propose moving it down the ladder — a lint rule, a test, a hook — instead of relying on instructions.

**Every change must be reproducible from committed state.** Never mutate untracked state: installed packages (`site-packages`, `node_modules`), cloned dependency source, global machine config. When a dependency is broken, escalate in order: pin/upgrade the version → the ecosystem's recorded patch mechanism (`pnpm patch`, Cargo `[patch]`, `go.mod replace`, uv `[tool.uv.sources]` → fork) → fork and point the manifest at it. All three leave a replayable record; patching in place leaves none. Litmus test: a teammate reproduces this from a fresh clone with one command.

**Mutate tool-owned files through the tool, never by hand.** If a tool regenerates or validates a file, that file is a projection of tool state — change it via the owning command: `uv add`, `pnpm add`, `go get` + `go mod tidy`, `cargo add`, `mise use`, `alembic revision` (list is illustrative; the criterion decides). The command validates, resolves, and keeps manifest and lockfile in sync; a hand edit silently desyncs them. Hand-edit only sections the tool has no command for (e.g. `[tool.*]` blocks in pyproject.toml). Lockfiles are never hand-edited.

**When blocked, no expedient hacks.** Do not monkey-patch, sleep to mask races, broaden exception handlers, weaken assertions, or special-case test inputs to force a pass. Fix the root cause; if you can't, stop and report the blocker. For third-party behavior that must change, escalate: official config/extension point → subclass or wrapper → fork (recorded, per reproducibility above). Runtime patching only in tests (`mock.patch`, scoped to the test).

**Errors fail loudly.** No fallbacks, defaults, or catch-and-continue on internal paths. Handle explicitly at system boundaries (user input, external APIs); everywhere else let errors propagate so they surface early. Discarding an error requires a specific reason and an inline comment marking it intentional.

**Precise types over escape hatches.** No `Any` / `any` / `interface{}` / `unknown` without a documented reason (genuinely dynamic shape, or an untyped third-party boundary) — they disable the checker for everything they touch. Prefer concrete classes, generics, `Protocol`/interfaces, unions, `TypedDict`.

**Single source of truth.** Every fact — constant, config value, schema, explanation — has exactly one authoritative home; everything else references it. Code: import, don't copy. Config: one declared value, derived everywhere else. Docs: each explanation lives in its closest home (symbol → doc comment; module rationale → module doc/ADR; runbook → external doc) and other documents link to it rather than paraphrase; prefer executable examples (doctest, Go `Example`) over prose ones — an example that runs cannot silently diverge. When you need a fact where it doesn't live, the fix is a reference or a move, never a second copy.

**Build what was asked — no speculative generality.** No config options, plugin points, or abstraction layers for hypothetical future needs. Introduce an abstraction at the second concrete use, not the first.

**A contract change isn't done until every caller is updated.** Enumerate call sites (LSP `findReferences` / `sg 'foo($$$)'`) — never patch just the ones you remember. Don't leave backwards-compat aliases or re-export shims unless asked.

**New dependencies clear a bar.** Prefer stdlib and already-declared deps. A new third-party package needs a reason: actively maintained, typed, and pulling in less than it saves.

**Write the language, not JavaScript.** Litmus test: if the code would translate to JavaScript nearly line-for-line, you are writing the intersection of all languages — rewrite with this language's native constructs. Frequent offenders (mostly restatements of the typing/error rules above):

| JS reflex                                       | Native idiom                                       |
| ----------------------------------------------- | -------------------------------------------------- |
| dict + `.get()` chains for structured data (Py) | `dataclass` / `TypedDict`; let `KeyError` surface  |
| manual accumulator loops (Py)                   | comprehensions / generators / itertools            |
| try/finally cleanup; string-built paths (Py)    | context managers; `pathlib`                        |
| defensive `if x is None` / `x or default` (Py)  | EAFP — let exceptions propagate                    |
| async by default (Py)                           | sync unless concurrency is real                    |
| `map[string]any` for structured data (Go)       | named structs                                      |
| `panic`/`recover` as try/catch (Go)             | error returns                                      |
| goroutines/channels as promises (Go)            | synchronous calls; `errgroup` for real concurrency |

## Tests

A behavior isn't tested until it has all three:

1. **Positive case** — valid input → expected output, asserted on specific values (not truthiness, not "no exception").
2. **Negative case** — invalid input → the specific error type/message, not just "raises".
3. **Boundaries** — empty / one / many / max / off-by-one.

Test names state the expected behavior. Mock only at system boundaries you don't own — a test that mocks the unit under test verifies nothing. Every test must be able to fail: asserting existence, signatures, or constants is not a test.

## Observability

_The app emits to stdout / a collector; it does not own routing, rotation, or shipping. Decide level and payload while writing the code._

- **Structured events, never interpolated prose** — `logger.info("order placed", order_id=id)`, not f-strings. Fields are queryable; sentences aren't.
- **Levels carry meaning**: ERROR = human must act / invariant broke; WARN = degraded but self-healing; INFO = business-significant transition; DEBUG = dev detail, off in prod. Tune verbosity by level, never by deleting logs.
- **Log once, at the boundary that handles.** Never log-and-throw — same failure recorded N times with no new information. Handle it (and log), or propagate it (and stay silent).
- **Never log secrets, tokens, PII, or full request/response bodies.** Redact at emission; log an identifier, not the value.
- **Propagate a trace/request id** through the call graph (Go: rides on `ctx`; elsewhere OpenTelemetry context).
- **Logs = events; metrics = aggregates; traces = cross-service causality.** Keep metric label cardinality bounded — no user ids / request ids / raw URLs in labels. OpenTelemetry is the vendor-neutral default.

| Language  | Structured logging           | Traces / metrics  |
| --------- | ---------------------------- | ----------------- |
| Python    | `structlog` (JSON to stdout) | OpenTelemetry SDK |
| Go        | `log/slog` (stdlib, 1.21+)   | OpenTelemetry Go  |
| TS / Node | `pino`                       | OpenTelemetry JS  |

## Comments & Documentation

_Governs committed, shared code; the local working tree is your own._

- **Comments carry _why_ and contract, never _what_** — if a comment explains what code does, prefer clearer code; keep it only for irreducible complexity, and say why the odd form is necessary.
- **Doc comments are the caller's contract**: signature + doc must suffice without reading the body — units/timezone, lifecycle, thread-safety, what it throws, side effects.
- **Change the code → change the comment in the same edit**; a comment you can't keep true gets deleted.
- **No commented-out code or inline changelogs** — git remembers (`blame`, `log -L/-S`). If the absence is non-obvious, leave a `NOTE` with the why.
- **Actionable debt gets a tagged owner or ticket**: `TODO(owner|#123)`. Long-lived work goes in the tracker, not buried prose.

## Code Search & Navigation — ripgrep / ast-grep / LSP

**ripgrep (the `Grep` tool) is the default for all code search** — identifiers, imports, log lines, exploration. Its false positives (comments, strings) are cheap to skim; don't pay pattern-syntax friction for precision the task doesn't need. Two exceptions:

- **Structural rewrite / codemod → `sg --rewrite`.** No text tool substitutes.
- **Enumerating call sites before a contract change → LSP `findReferences` first** (resolves imports/aliases; text search hits same-named strings), then `sg 'foo($$$)'` when no language server covers the language, then ripgrep + manual filtering as the last resort.

LSP is also the tool for other symbol-identity questions: go-to-definition/implementation, call hierarchy, hover for type info. On LSP error, fall back down the chain and say so.

**An empty ast-grep result never proves absence** — a mistyped pattern silently matches nothing. On a surprising miss, fall back to ripgrep and say so; never report "not found" from an ast-grep miss alone.

## Subagents & Workflows

Try direct search first — most lookups resolve in one Grep/Glob/WebFetch. Spawn agents for genuine fan-out, not for things you could find in 30 seconds.

**Always set the model explicitly** — the Agent tool's `model` param, and `opts.model` on every Workflow `agent()` call. Workflow's `agent()` otherwise silently inherits the main-loop model, bypassing this table:

| Task type                                               | Model    |
| ------------------------------------------------------- | -------- |
| Web fetching, summarization, file search, log scanning  | `haiku`  |
| Code reasoning, architecture analysis, code generation  | `sonnet` |
| Multi-file planning/refactoring, cross-file consistency | `opus`   |

Unsure? Ask "judgment, or just retrieval/aggregation?" — retrieval means `haiku`. `sonnet`/`opus` boundary tie-breaks to `opus` when the change crosses files. `fable` is never a default: it is per-case escalation for reasoning beyond Opus's ceiling, at significantly higher cost — when in doubt, `opus` is the top of the table.

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

## Interface & API Design

- AIP (https://google.aip.dev) is the source of truth. Two layers:
  - Its **principles** — consistency, standard verbs, resource naming, pagination thinking — generalize to REST, gRPC, CLI, and internal interfaces alike.
  - Its **concrete rules** — HTTP mapping, field masks, LROs, `:customVerb` — are for resource-oriented network APIs (gRPC/protobuf, Google-style REST). Don't force RPC/streaming/GraphQL/CLI into them.
- When citing or applying a **specific AIP rule** to a resource-oriented API, fetch and read it first — don't work from memory of AIP numbers. General interface design needs only the principles layer.
