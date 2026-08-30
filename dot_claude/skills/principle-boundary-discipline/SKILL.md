---
name: principle-boundary-discipline
description: "Architecture. Apply when wiring validation, error handling, or framework adapters."
---

# Boundary discipline

Place validation, type narrowing, and error handling at system boundaries. Trust internal code unconditionally. Business logic lives in pure functions; the shell is thin and mechanical.

**Why:** Scattered validation is noisy, redundant, and gives a false sense of safety. Validate data once at the boundary. Keep logic out of framework wiring so it can be tested without the framework.

**The pattern:**
- **At boundaries** (CLI args, config files, external APIs, network protocols, user input): validate, return errors, handle defensively.
- **Inside the system:** typed data, error propagation, no re-validation. Trust the types. No redundant nil checks deep in call chains if the boundary already validated.
- **Across the boundary:** expose domain concepts, not the boundary's private representation. Do not re-export transport, storage, framework, or wire types through the public surface. Keep general-purpose mechanism inside and special-purpose policy at the edge.
- **Business logic in pure functions** with no framework dependencies: parsing as a pure transform from raw bytes to typed state, construction as structured state in / output out, scoring and assessment as pure transforms from state to results. The shell just calls them.

**Errors fail loudly.** No fallbacks, defaults, or catch-and-continue on internal paths. Handle explicitly at the boundaries above; everywhere else let errors propagate so they surface early. Discarding an error requires a specific reason and an inline comment marking it intentional.

**The tests:**
- "Is this data crossing a system boundary right now?" If not, validation is redundant.
- "Can this be a pure function that the shell just calls?" If yes, extract it.
