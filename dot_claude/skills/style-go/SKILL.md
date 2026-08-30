---
name: style-go
description: "Code & delivery. Apply when writing or reviewing Go. Run the golangci baseline (diff-scoped in foreign repos); the %w chain is contract; goroutines have owners, bounds, and an error destination; interfaces belong to the consumer; zero values are designed; version-gated idiom via use-modern-go."
---

# Go style

## Oracles first

- **Baseline**: `golangci-baseline.yml` next to this file enforces the mechanical half of this skill; scope per engineering's repo regimes — Go's diff-scoped run is `golangci-lint run --new-from-rev=<base>`. Prose below carries only what linters cannot.
- **Version-gated idiom** (which stdlib construct exists at this `go` directive): load `use-modern-go`. It applies to lines you write — never license to modernize surrounding code.
- **Generation**: tools pinned via go.mod `tool` directive (`go get -tool`), invoked by `//go:generate go tool <name> ...`, so a fresh clone reproduces with `go generate ./...`; CI gates with `go generate ./... && git diff --exit-code`.
- Test-time: `goleak` for goroutine leaks. `gofmt` on whole files you create or heavily edit is always safe — universal in Go, unlike Python formatters.

## Errors

- Intentional drop is `_ = w.Close() // why it's safe here`. errcheck finds the bare call; the why-comment is on you.
- **The `%w` chain is contract.** Callers can `errors.Is/As` anything you wrap, so at published boundaries the chain carries only your layer's vocabulary (`fmt.Errorf("loading profile %s: %w", id, ErrNotFound)`); the foreign cause (`sql.ErrNoRows`) goes into the message via `%v`, logged once at the boundary that handles it. `fmt.Errorf` takes multiple `%w` since 1.20 — wrapping the foreign error alongside re-leaks it into contract.
- Export a matchable error (sentinel or type) only when callers branch on it. Doc-comment the error set of exported functions. An error your own code produces or translates is a behavior — its negative-path test is ordinary test coverage; a merely propagated error is documented, not tested.
- `errors.Join` for same-layer aggregation: collected validation failures, parallel failures, and the write-path defer `defer func() { err = errors.Join(err, f.Close()) }()` — a swallowed Close on a write path is a dropped error. `Is/As` match any branch of a join, so joining foreign errors extends contract too.
- When callers must enumerate outcomes, promote expected failure modes out of `error` into the return value as a sealed sum type (below); `error` stays the open set for the unexpected.

## Sum types & enums

- Variants carrying data: sealed interface (`type Event interface{ isEvent() }`) marked `//sumtype:decl` — gochecksumtype forces type switches to cover every variant.
- Scalar enums: named type + generated const block. Regime-1 tool: `go-enum` (via tool directive) for String/Parse/Marshal; foreign repos use whatever the repo already generates with. The zero value is either the meaningful default or `XxxUnspecified = 0`. External input enters through `ParseXxx` at the boundary.
- Exhaustive switches list every member **and** keep a fail-loud `default:` — the linter guards known members, the default guards out-of-range values (Go enums aren't closed). One enum style per repo.

## Concurrency

- Goroutine lifetimes nest in their creator's scope: `Wait()` before return, or return a handle with a named owner. Concurrency is an implementation detail behind a synchronous API.
- **Every fan-out has an explicit bound** — `errgroup.SetLimit` (weighted: `semaphore.Weighted`). A hand-built channel worker pool is for long-lived stream consumers only, with explicit close/wait ownership. Bound sizing: CPU-bound → GOMAXPROCS; IO-bound → the downstream resource limit, not CPU.
- Goroutine errors have a destination — errgroup (`WaitGroup.Go` for the no-error case). `go func() { work() }()` discarding a returned error is a dropped error.
- Tests synchronize via channels or `testing/synctest` (go ≥1.25) — `time.Sleep` is never synchronization.
- `sync.Pool` only with profile evidence or an obviously hot allocation site; it is object reuse, not a cache.

## Construction & interfaces

- Interfaces belong to the consumer: defined at the use site, only the methods used, abstracted at the second implementation. Accept interfaces, return structs.
- Zero value designed: either it works, or unexported fields + constructor make construction mandatory — the half-measure (constructor plus nil-guards everywhere) is the bug.
- Wiring: hand-wire in `main` by default — **principle-explicit-dependencies** carries the invariants (gochecknoglobals/gochecknoinits are its lint shadow).
- HTTP clients and servers get explicit timeouts; bare `http.Get` and zero-value `http.Server` don't ship.
- Signals (logs, metrics, spans): load `principle-observability` before emitting. Its Go grounding: emit through `log/slog` directly; scope fields via `logger.With(...)`; ambient fields via a ctx-aware `Handler`; semconv attribute names from `go.opentelemetry.io/otel/semconv`.
- Tests default to table-driven, `if got != want` / `cmp.Diff`; assertion libraries are regime-1 opinionated tooling — follow the repo's incumbent style.
- Packages are named by the capability they provide; `utils`/`helpers`/`common` is a naming failure (low severity — fix when touching).

**Write Go, not JavaScript/Java.** Litmus test: if the code would translate nearly line-for-line, rewrite with native constructs:

| reflex                                  | native idiom                                       |
| --------------------------------------- | -------------------------------------------------- |
| `map[string]any` for structured data    | named structs                                      |
| `panic`/`recover` as try/catch          | error returns                                      |
| goroutines/channels as promises         | synchronous calls; `errgroup` for real concurrency |
| producer-side interface, getters/setters | consumer-side interface, exported fields           |

## Judgment residue (no oracle exists)

Package boundary cuts; when a detached context is honest (background work outliving the request); who owns cancellation and where concurrent errors converge. Decide explicitly; the why goes in a comment.
