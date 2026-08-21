---
name: engineering
description: "MUST load at the start of any engineering task: writing, changing, reviewing, or designing code that will run or be kept — anything beyond a one-off throwaway. The discipline and operating procedures for engineering work; each principle is its own `principle-*` skill, loaded when you apply it."
---

# Engineering

Each principle is its own skill, listed alongside this one and prefixed by area — Core, Architecture, Verification, Code & delivery. **Load a principle's skill with the Skill tool when you apply it**: the name is the compressed rule, the skill body is the full one.

## The discipline

- **Cite what changed a decision.** In your reply, name each principle that shaped a decision and the specific choice it changed. A citation with no decision behind it means you never loaded it; it must trace to a real choice the rule drove.
- When work matches a principle and you deliberately deviate, say why in one line.

## Dependencies clear a bar

No skill of its own — this section is the rule. Prefer stdlib and already-declared deps. A new third-party package needs a reason: actively maintained, typed, and pulling in less than it saves.

## Operating procedures

**Code search & navigation.** ripgrep (the Grep tool) is the default for all code search — its false positives are cheap to skim. Two exceptions: structural rewrite/codemod → `ast-grep --rewrite`; enumerating call sites before a contract change → LSP `findReferences` first, then `ast-grep 'foo($$$)'` where no language server covers the language, then ripgrep + manual filtering. LSP owns symbol-identity questions (definition, implementations, call hierarchy, hover). An empty ast-grep result never proves absence — on a surprising miss, fall back to ripgrep and say so.

**Delegation.** Spawn a subagent when all three hold: compression ratio ≥ ~10:1 (tokens read ÷ tokens kept), above the ~5-file overhead floor, and the reads are discardable — otherwise search directly. Route bulk output to subagents; the main thread keeps summaries, not raw payloads. Set the model explicitly on every spawn: retrieval/aggregation → haiku; code search/explanation → sonnet; code reasoning/generation → sonnet; cross-file planning/refactoring → opus; adversarial quality review → opus; conformance verification / oracle construction → opus (hook-enforced by require-agent-model; the model-pinned agent defs in ~/.claude/agents/ cover these classes). When fanning out, write one contract file all delegates read — recipe, verification contract, do-not-touch fences — kept outside their write scope (principle-build-the-lever). Tell engineering delegates to load `/engineering`. Verify delegated work by artifact, not the delegate's summary (principle-prove-it-works).
