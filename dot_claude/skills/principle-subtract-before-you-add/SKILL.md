---
name: principle-subtract-before-you-add
description: "Core. Apply when sequencing an addition, refactor, or rewrite. Remove dead weight first, then build on the simpler base."
---

# Subtract before you add

When evolving a system, remove complexity first, then build. Deletion gives you a simpler base, which makes the next addition smaller and less brittle.

**Why:** Adding to a complex system compounds complexity. Removing first cuts the surface area, reveals the essential structure, and usually makes the next design obvious. Default to subtraction.

**The pattern:**
- Sequence removal before construction: dead code, unused options, and redundant guards go first, ideally as their own commit.
- Cut before you polish (get to the minimum before investing in quality)
- Make simplification a continual investment: leave the design slightly simpler and more capable behind the same or smaller surface than you found it.

**The test:** name what the diff deleted before it built. If the answer is nothing and the base wasn't already minimal, the subtraction pass was skipped.

Deciding what not to build at all is **principle-laziness-protocol**; this leaf orders the work.
