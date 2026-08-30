---
name: principle-observability
description: "Code & delivery. Apply when writing code that handles a request, job, or message, calls external services, adds retry/fallback/timeout logic — or is about to emit any log, metric, or span."
---

# Observability

This principle governs producing signals inside the program. Collectors, routing, dashboards, and alerting are infrastructure — out of scope. The app emits to stdout / a collector and owns nothing downstream; emission is code, configuration belongs to the composition root.

**Record the nondeterminism; test the determinism.** A signal answers questions rerunning the code cannot: which path ran, how long, how many retries, what the external system returned. Pure computation gets tests, not logs.

**The unit is the operation, not the line.** Each unit of work emits one wide event at the layer that owns its outcome — the place that decides success, failure, or retry. Interior milestones become fields (`retry_count`, `fetch_ms`), never separate narrative lines. Failure is not a second concern: it is `outcome=error` plus the error chain in the same event — the owner logs once, everyone below stays silent (no log-and-rethrow).

**Signal sites** — a signal is warranted exactly at:

- a unit entry point — handler, consumer, job, CLI command, and the process itself (startup event with version and config digest; shutdown event with reason; a top-level crash handler emits the failure branch) → the wide event
- a runtime decision that changes the path — retry, fallback, circuit open, cache rebuild, degraded mode → a field of the owning event; its own event only when no owning event will carry it
- an irreversible external effect or a domain state transition → INFO
- a broken invariant → ERROR

Interior computation emits nothing.

**Fields have provenance** — one source each:

- **ambient** (request id, trace id, tenant, version): stamped by middleware / logger context at the boundary; business code never re-attaches them. Unit durations come from the span or middleware, not hand-rolled subtraction.
- **scope**: bound once at scope entry, inherited by every event within.
- **event-own**: given at emission — each event carries only its own facts.

**Names are derived, not typed.** Follow the OpenTelemetry Semantic Conventions where one exists — imported from the generated semconv constants package, never re-typed as string literals. Invented domain names carry units (`fetch_ms`, `payload_bytes`) and live in one constants module. A field name, once emitted, is a contract with the org's queries — renaming it is an API change. When signals acquire consumers beyond your own dashboards, the vocabulary graduates to a declared semconv registry: OTel Weaver validates it and generates the constants and docs, and telemetry schema versions carry renames instead of silent breaks. Log identifiers, not values; never secrets, tokens, PII, or full request/response bodies.

**Levels carry meaning**: ERROR = human must act / invariant broke; WARN = degraded but self-healing; INFO = business-significant transition; DEBUG = dev detail, off in prod. Tune verbosity by level, never by deleting emission sites.

**Choose the signal.** The wide event is the default. Add a metric only when an aggregate needs an alert or SLO — RED (rate, errors, duration; histograms, not averages), labels bounded; aggregates the collector can derive from events are not hand-rolled. High cardinality (user ids, request ids) is welcome in events and spans, forbidden in metric labels. When the repo traces, each unit gets a span and every event carries the trace id, so logs join traces. OpenTelemetry is the vendor-neutral default.

**The tests:**

- Could rerunning this code with the same inputs answer what this signal answers? Delete it.
- If this operation failed at 3 a.m., does its one event say who, what, which path, how long, why?
- Is anything emitted inside a loop, or from pure logic?
- Is this field already stamped by middleware or the scope above?
- Would two of these lines be one event with more fields?

The style leaves (**style-python**, **style-go**) ground emission in specific syntax and carry the lintable half.
