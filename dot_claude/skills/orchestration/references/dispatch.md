# Dispatch and collection

Read when actually sending work out — to subagents, forks, or workflow stages.

## The briefing contract

A dispatched unit's prompt is a brief, and the brief is the unit's entire world: subagents see no conversation history, so anything not in the brief does not exist. A brief carries:

- **Scope** — one problem domain, named artifacts (paths, symbols), and the boundary stated positively ("fix tests only", "the oracle is read-only input").
- **Context** — error output, decisions already made, adjudicated tradeoffs. Write it as if briefing someone who just walked in, because that is exactly the situation.
- **Output contract** — when a machine consumes the result, give a schema and require "your entire final message is exactly this JSON"; when a human consumes it, require file:line references and evidence.
- **Model** — set explicitly on every spawn; definition pins are the default, override per task difficulty. The routing table lives in the engineering index.
- **Discipline** — tell engineering delegates to load `/engineering`.

When several delegates share a recipe, write one contract file they all read — recipe, verification contract, do-not-touch fences — kept outside their write scope (principle-build-the-lever).

Sequencing knowledge stays out of the brief. A worker told "assume the code is linted" breaks silently the moment the pipeline changes; the worker covers its whole domain and the process deduplicates.

## Batching

One message with many Agent calls runs them in parallel; one call per message runs them serially. Keep briefs self-contained so completion order cannot matter. Background subagents return through notifications — save each result and its usage numbers as they arrive; notification data is not persisted anywhere else.

## Fork vs subagent

A fork inherits the whole conversation: use it for work that needs the discussion — filing tickets from a plan just made, drafting the summary of a decision. Use a fresh subagent for everything sliceable, and always when T2 demands unseen reasoning.

## Collection

Verify by artifact, not by the worker's summary — a delegate's "done" is a claim, and claims route to oracle (principle-prove-it-works). Check cross-unit conflicts before integrating; the same file touched by two units means T5 was violated at slicing time, so fix the slicing, not the merge. A node used repeatedly deserves a measured error profile: grade a sample of its output against ground truth once, rather than trusting its style forever.
