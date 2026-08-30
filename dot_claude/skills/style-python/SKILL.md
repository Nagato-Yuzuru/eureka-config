---
name: style-python
description: "Code & delivery. Apply when writing or reviewing Python. Run the ruff baseline + ty check (diff-scoped via lint-diff.py in foreign repos); type until illegal states don't check; parse, don't grope; capabilities enter via __init__; TaskGroup-scoped concurrency; generator pipelines for item streams. Env/package commands live in CLAUDE.md (uv table)."
---

# Python style

## Oracles first

- **Baseline**: `ruff-baseline.toml` beside this file, plus `ty check` (mypy only as a foreign repo's incumbent). Scope per engineering's repo regimes; `~/.config/ruff/ruff.toml` carries the same core and is ruff's automatic fallback when a repo has no config, so regime 2/3 runs are `uv run lint-diff.py <base> [--config …]` (script beside this file). Prose below carries only what the checkers cannot.
- **Every third-party import is declared via `uv add` in the same change** — code importing an undeclared package is incomplete. Audit with `uv run --with deptry deptry .` (never via `uvx`: an isolated env mis-guesses module names, e.g. `pyyaml`→`yaml`). In a monorepo run it per package; whitelist runtime-only backends via `[tool.deptry.per_rule_ignores]`.
- Version-gated modernization is mechanical: ruff's UP family follows the repo's `requires-python`.

## Types — until illegal states don't check

- Variants are unions of `@dataclass(frozen=True, slots=True, kw_only=True)`. `kw_only` is mandatory — dataclasses have no positional-independence, so field evolution breaks positional callers; math-like two-field tuples (`Point(x, y)`) may skip it. Mutability is opt-in, never the default.
- **Every `match` over a closed union ends in `case _ as unreachable: assert_never(unreachable)`** — exhaustiveness becomes a `ty` failure instead of a convention.
- Brand semantic primitives: `NewType` when construction needs no validation; pydantic `RootModel` when it does. Small closed sets: `Literal`; domain enums: `StrEnum`. Generics in PEP 695 syntax (3.12+).
- An `isinstance` ladder is a modeling failure: dispatch with `match` or `functools.singledispatch`.
- Accept the narrowest interface, return concrete types. `Protocol`s are defined at the point of use and kept small — Python has no type intersections, so a consumer needing two capabilities takes two parameters or declares a combined Protocol; no mixin towers. Structured data is never bare `dict`/`list` — `TypedDict`, or a dataclass when you own the shape.

## Errors

- Libraries raise their own exception hierarchy (package base + specific subclasses); `ValueError`/`TypeError` stay reserved for argument-contract violations. Re-raising across a layer is `raise OwnError(...) from err` — always `from`.
- Errors fail loudly: `except` handles what this layer can actually handle; everything else propagates. Catching to substitute a default and continue is the defect. Intentional suppression is `with contextlib.suppress(X):  # why` — visible, never a silent `pass`.

## Docs

- Docstrings are Google style; a repo's incumbent convention (numpy/reST) wins.
- The signature carries types, the name carries intent; a docstring exists only for contract neither can express — and before writing one, try lifting the fact into the type (`Annotated`, `NewType`) first. What survives is the true residue: units, tz-awareness, ordering and lifetime guarantees.
- `Raises:` names the exceptions callers may need to handle. An exception your own code raises or translates is a behavior — its negative-path test is ordinary test coverage (usually mock-free: those sites are logic); a merely propagated exception is documented, not tested.
- A docstring that restates the signature is worse than none.

## Boundaries & pydantic

- **Pydantic is the boundary parser — parse, don't validate.** External data (JSON, env, args, rows) becomes typed models once at the edge; internal logic runs on the results (frozen dataclasses when re-validation isn't real). Inbound contracts: `ConfigDict(frozen=True, extra="forbid")`; variants as discriminated unions; non-BaseModel shapes via `TypeAdapter`.
- The parsing vocabulary is shared: recurring field logic (time parsing above all) lives once as `Annotated` types in one module the whole codebase imports.
- Parse, don't grope: `.get("a", {}).get("b")` chains turn missing data into wandering `None`s. Model the shape; over large documents declare paths (jmespath/glom); msgspec/orjson only after measuring.
- `os.environ` is a capability: one settings object (pydantic-settings) built at the composition root; deep code never reads the environment.

## Construction

- Capabilities — clock, fs, network, storage, other services — enter through `__init__`/parameters typed as the narrowest Protocol they use; wiring and resource lifecycles (with shutdown hooks) converge at the composition root. Load `principle-explicit-dependencies` for the invariants. Patching your own module in a test is the tell of a missing injection point.
- Signals (logs, metrics, spans): load `principle-observability` before emitting. Its Python grounding: emit through `structlog` directly — a homegrown logger-wrapper module is the `utils` package of logging; ambient fields via `structlog.contextvars`; semconv attribute names from `opentelemetry.semconv`; a hand-measured interval uses `time.monotonic()`.
- Libraries acquire a logger and emit; configuration (processors, handlers, `basicConfig`) belongs to the composition root alone.
- A recurring setup/teardown pair is a context manager; dynamic sets compose with `ExitStack`. One-off cleanup in `try/finally` is fine.
- Imports live at module top. A circular import is a boundary drawn wrong — reflect on the structure first; a cycle-breaking local import carries a comment stating why it must exist (the only other commented exception: keeping a heavy/optional dependency lazy).

## Streams & bulk

- A stream of items transforms as generator stages (`yield` per stage) — lazy, constant-memory, early-terminating. Materialize when you need `len`, random access, or multiple passes.
- Bulk columnar work (joins, group-bys, aggregation) goes to an engine — polars/duckdb/numpy — not hand-rolled dict loops; plain Python is right for small-n munging. Engine deps are regime-1 tooling.

## Concurrency

- Sync is the default; async only when IO-concurrency is real. Concurrent tasks live in an `async with asyncio.TaskGroup()` scope — first failure cancels siblings and surfaces as an `ExceptionGroup` (`except*`); timeouts are scopes too (`asyncio.timeout`), not scattered `wait_for`. `gather` is the legacy reflex.
- Blocking-IO parallelism: threads. CPU parallelism: processes (free-threading is a version-gated future, not a default).

**Write Python, not JavaScript.** Litmus test: if the code would translate to JavaScript nearly line-for-line, you are writing the intersection of all languages — rewrite with native constructs:

| reflex                                     | native idiom                                      |
| ------------------------------------------ | ------------------------------------------------- |
| dict + `.get()` chains for structured data | typed model at the boundary; let `KeyError` surface |
| manual accumulator loops                   | comprehensions / generator stages / itertools     |
| try/finally boilerplate; string-built paths | context managers; `pathlib`                      |
| defensive `if x is None` deep inside       | EAFP — the boundary already parsed                |
| returning `None` for failure               | raise, or model the absence in the type           |
| async by default                           | sync unless concurrency is real                   |

## Judgment residue (no oracle exists)

Package boundaries and the public surface (`__init__.py` re-exports and `__all__` are deliberate design); when a pipeline materializes; pydantic model vs plain dataclass at internal seams. Decide explicitly; the why goes in a comment.

For worked shapes — capability injection with shutdown hooks, TaskGroup skeleton, shared Annotated vocabulary, RootModel branding, generator pipeline — read `references/patterns.md`.

Environment and package-management commands (uv table, PEP 723 scripts) live in CLAUDE.md.
