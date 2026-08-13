---
name: engineering
description: "MUST load at the start of any engineering task: writing, changing, reviewing, or designing code that will run or be kept — anything beyond a one-off throwaway. The index of engineering principles and operating procedures; read a principle's leaf skill in full when you apply it."
---

# Engineering

This is the index. Each principle lives in a leaf skill at `~/.claude/skills/<name>/SKILL.md`; the name is the compressed rule, the leaf is the full one.

## The discipline

- **Read the leaf in full for any principle you apply.**
- **Cite what changed a decision.** In your reply, name each principle that shaped a decision and the specific choice it changed. A citation with no decision behind it means you skipped its leaf; it must trace to a real choice the rule drove.
- When work matches a principle and you deliberately deviate, say why in one line.

## Principles

- **Core** — principle-laziness-protocol · principle-foundational-thinking · principle-redesign-from-first-principles · principle-subtract-before-you-add · principle-minimize-reader-load · principle-exhaust-the-design-space · principle-build-the-lever
- **Architecture** — principle-model-the-domain · principle-boundary-discipline · principle-type-system-discipline · principle-make-operations-idempotent · principle-migrate-callers-then-delete-legacy-apis · principle-separate-before-serializing-shared-state · principle-single-source-of-truth · principle-interface-design
- **Verification** — principle-prove-it-works · principle-fix-root-causes · principle-sequence-verifiable-units
- **Code & delivery** — principle-testing · principle-observability · principle-comments-and-docs · principle-reproducible-state · style-python · style-go
- **Dependencies clear a bar** (no leaf; this line is the rule) — prefer stdlib and already-declared deps. A new third-party package needs a reason: actively maintained, typed, and pulling in less than it saves.

## Operating procedures

**Code search & navigation.** ripgrep (the Grep tool) is the default for all code search — its false positives are cheap to skim. Two exceptions: structural rewrite/codemod → `ast-grep --rewrite`; enumerating call sites before a contract change → LSP `findReferences` first, then `ast-grep 'foo($$$)'` where no language server covers the language, then ripgrep + manual filtering. LSP owns symbol-identity questions (definition, implementations, call hierarchy, hover). An empty ast-grep result never proves absence — on a surprising miss, fall back to ripgrep and say so.

**Delegation.** Spawn a subagent when all three hold: compression ratio ≥ ~10:1 (tokens read ÷ tokens kept), above the ~5-file overhead floor, and the reads are discardable — otherwise search directly. Route bulk output to subagents; the main thread keeps summaries, not raw payloads. Set the model explicitly on every spawn: retrieval/aggregation → haiku; code reasoning/generation → sonnet; cross-file planning/refactoring → opus (hook-enforced by require-agent-model). When fanning out, write one contract file all delegates read — recipe, verification contract, do-not-touch fences — kept outside their write scope (principle-build-the-lever). Tell engineering delegates to load `/engineering`. Verify delegated work by artifact, not the delegate's summary (principle-prove-it-works).
