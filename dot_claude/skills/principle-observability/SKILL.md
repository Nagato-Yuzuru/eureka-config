---
name: principle-observability
description: "Code & delivery. Apply when adding logging, metrics, or traces. Structured events, never interpolated prose; levels carry meaning; log once at the boundary that handles; never log secrets/PII; propagate a trace id; logs=events, metrics=aggregates, traces=causality."
---

# Observability

The app emits to stdout / a collector; it does not own routing, rotation, or shipping. Decide level and payload while writing the code.

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
