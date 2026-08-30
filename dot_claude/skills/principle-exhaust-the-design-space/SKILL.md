---
name: principle-exhaust-the-design-space
description: "Core. Apply when a design decision would be expensive to reverse and you can name a second plausible shape."
---

# Exhaust the design space

When a decision is expensive to reverse and more than one shape would work, put the alternatives side by side before committing to the first one you thought of. Building the wrong thing costs more than sketching three.

**The trigger is self-checkable.** Can you name a second plausible shape? If no second shape comes to mind, there is no design space to exhaust — build the obvious thing and move on. If one does, and reversing the choice later would mean a rewrite rather than an edit, sketch both before committing.

**A candidate is cheap.** Five lines of type signatures on a scratch file, or two paragraphs of pseudocode, is a candidate. You are comparing shapes, not running code: a working prototype is the ceiling, not the entry price. A second flavor of the first shape is not a second candidate.

**When it doesn't apply:**
- Mechanical implementation where the pattern is established
- Bug fixes or refactors with a clear target state
- Changes where constraints dictate a single viable approach

**The test:** state in one line each what the losing candidates were and why they lost. If you can't, you didn't compare — you wrote down the first idea and kept going.
