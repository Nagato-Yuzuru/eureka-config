---
name: principle-explicit-dependencies
description: "Architecture. Apply when a component acquires collaborators or capabilities (clock, fs, network, storage, other services), when choosing a DI mechanism, or when a test reaches for monkeypatching — the tell that a dependency isn't injectable. Capabilities enter via construction; one grep-able composition root; core code never knows the wiring mechanism; inject only what a test would replace."
---

# Explicit dependencies

Components receive their dependencies; they never reach into the environment for them. DI/IoC frameworks are one possible mechanism — these invariants are the principle, and they hold under hand-wiring and containers alike:

1. **Capabilities enter explicitly.** Anything that does I/O or holds state a test would replace — clock, fs, network, storage, other services — arrives via constructor or parameter. Package-level singletons and service locators reached from deep code violate this; scattered `container.get(X)` at use sites is a locator whatever the library calls itself.
2. **One composition root.** "Who provides X" is answered by reading one grep-able place: `main()`, a wiring module, a declarative container class. Provider graphs discovered by reflection or classpath scanning fail the test.
3. **Core code never knows the mechanism.** Tests construct core types plainly, without the container; injection machinery appears only at the edge.
4. **Inject only what a test would replace.** Pure functions, value types, and stdlib are called, not injected — over-injection rebuilds interface pollution from the far side (the Spring reflex).

**Mechanism is a project decision, not a principle.** Hand-wiring, codegen (wire), a declarative container (dependency-injector) — teams legitimately differ; the choice lives in the project's CLAUDE.md/ADR, and foreign repos keep their incumbent. Greenfield default: hand-wire until wiring pain is real.

Mechanical shadow: Go's `gochecknoglobals`/`gochecknoinits` enforce the package-level half of invariant 1. Related: **principle-boundary-discipline** (pure core), **principle-testing** (mocking only boundaries you don't own presupposes injectable deps), **principle-laziness-protocol** (no container before wiring pain).
