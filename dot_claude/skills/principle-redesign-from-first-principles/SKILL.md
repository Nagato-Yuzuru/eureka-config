---
name: principle-redesign-from-first-principles
description: "Apply when integrating a new requirement into an existing design, or when implementation keeps producing the same shape of friction. Redesign as if the requirement had been a day-one assumption instead of bolting it on; scrap designs that fight back."
disable-model-invocation: true
---

# Redesign from first principles

When integrating a change, don't bolt it onto the existing design. Redesign as if the requirement had been there from the start. The result should look like what we would have built if we'd known on day one.

- Read all affected files and understand the current design holistically
- Ask: "if we were writing this from scratch with this new requirement, what would we build?"
- Propagate the change through every reference: types, docs, examples, rationale sections
- Think about the redesign holistically, then deliver it incrementally

## The implementation-time trigger: scrap wrong designs

If implementation keeps producing friction the design can't absorb, throw the design out instead of patching it. **The signal is a pattern, not single instances.** Tells:

- The same shape of workaround appearing repeatedly across unrelated code.
- Multiple unrelated edge cases that all need special-case branches.
- Types that need escape hatches (`any`, casts, optional fields always set in practice) to compile.
- The "we need a lock" reflex when the design said the state wasn't shared.
- Callers having to know the abstraction's internal rules to use it.

Use judgment. A few hard cases don't condemn an architecture; some problems are legitimately complex, and complexity in the data is not complexity in the design.

**When you scrap:**

1. Re-derive the constraints from what's been built. The implementation lessons enter the new design as inputs, not vibes.
2. Redesign as if the new constraints had been day-one assumptions.
3. Subtract before adding: the new design should be smaller than the old one before it grows.
4. Migrate in one wave; don't keep the old shape alive alongside the new one (see **principle-migrate-callers-then-delete-legacy-apis**).
