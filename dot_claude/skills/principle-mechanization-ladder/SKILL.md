---
name: principle-mechanization-ladder
description: "Apply when you catch yourself writing the same instruction or correction a second time. Prose is the enforcement of last resort: encode the convention as types > linter/formatter > tests > hooks/CI > instructions, picking the strongest rung the situation allows."
disable-model-invocation: true
---

# Mechanization ladder

Prose is the enforcement of last resort. To encode a convention, prefer (strongest first): **types > linter/formatter > tests > hooks/CI > instructions**.

**Why:** Textual instructions require the reader to notice, remember, and comply. Structural mechanisms (a type that won't compile, a lint that fails CI, a hook that blocks the call) enforce the rule without cooperation.

**Pick the strongest rung the situation allows.** Agents copy whatever the surrounding code already does, so a weaker guard becomes the next template. An unrepresentable state that cannot compile beats a lint or banned API that fails CI, which beats a canonical helper, which beats a runtime check, which beats prose.

**When you repeat yourself, move the rule down the ladder.** When you catch yourself writing the same instruction a second time, or notice a recurring correction:
1. Ask: can this be a type, a lint rule, a test, a hook, or a script?
2. If yes, encode it and delete the instruction.
3. If no (genuinely requires judgment), make the instruction more prominent and add an example of the failure mode.

**The instruction is the symptom.** If the fix is structural, only use the structural fix. Don't keep the prose alongside as a second copy.

**Feedback loop:**
- Capture every correction: when the human intervenes or tests fail, decide if it's a one-off or a pattern.
- Route to the right layer: one-off → note; recurring fix → lint rule or skill; systemic issue → principle.
- Close the loop: don't just record — encode it now or create a concrete todo.

**Anti-patterns:**
- Acknowledging without recording ("I'll keep that in mind" does not persist)
- Recording without routing (a note about a lint rule that should exist is wasted until the lint rule exists)
- Fixing without generalizing (fixing one instance while leaving the recurring pattern intact)
