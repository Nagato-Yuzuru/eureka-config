---
name: principle-laziness-protocol
description: "Core. Apply when scoping a change, or when tempted to add a config option, plugin point, or abstraction layer, or to thread a new signal through existing layers."
---

# Laziness protocol

Writing code is cheap for you, which makes over-engineering nearly free to produce and expensive for someone else to live with. Counter it by borrowing a human maintainer's fatigue.

**Why:** Speculative generality is paid for by every later reader, and the future it was built for usually doesn't arrive. An abstraction with one caller is a guess; the same abstraction at the second caller is evidence. Out-of-spec features drag validators behind them: persistence, retry-on-startup, and schema migration each need guards to defend their inputs.

**The pattern:**

- **Build what was asked.** No config options, plugin points, or abstraction layers for hypothetical future needs; no speculative validators, parsers, or guards beyond what the spec demands — design for observed usage. Introduce an abstraction at the second concrete use, not the first. When the request itself names a future ("we'll probably want YAML later"), satisfy it in the smallest shape that works today — a dict literal, not a registration API.
- **Minimize the diff.** The smallest change that solves the problem beats the elegant restructuring nobody asked for.
- **Question the threading.** If a task asks you to pass a new signal through types, schemas, or pipeline layers, stop and look for a path that doesn't need the signal at all.

**The test:** for every abstraction, option, or layer in your diff, name the second concrete caller that needs it. If you can't name one, delete it.

**Prime directive:** If a human developer would find the code exhausting to maintain, it is a bad solution. Be lazy. Stay simple.

Sequencing removal before construction is **principle-subtract-before-you-add**; counting layers and held state is **principle-minimize-reader-load**; one authoritative home per fact is **principle-single-source-of-truth**.
