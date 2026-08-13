---
name: principle-single-source-of-truth
description: "Apply when a constant, config value, schema, or explanation is about to exist in two places. Every fact has exactly one authoritative home; everything else references it. Code: import, don't copy. Config: derive. Docs: link to the closest home."
disable-model-invocation: true
---

# Single source of truth

Every fact — constant, config value, schema, explanation — has exactly one authoritative home; everything else references it.

- **Code: import, don't copy.**
- **Config: one declared value, derived everywhere else.**
- **Docs: each explanation lives in its closest home** (symbol → doc comment; module rationale → module doc/ADR; runbook → external doc) and other documents link to it rather than paraphrase. Prefer executable examples (doctest, Go `Example`) over prose ones — an example that runs cannot silently diverge.

When you need a fact where it doesn't live, the fix is a reference or a move, never a second copy.

Related: **principle-laziness-protocol** (consolidate decisions behind one source), **principle-type-system-discipline** (derive types from authoritative schemas instead of hand-rolling parallels).
