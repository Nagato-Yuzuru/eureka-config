---
name: principle-make-operations-idempotent
description: "Architecture. Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries."
---

# Make operations idempotent

Design operations so they converge to the correct state regardless of how many times they run or where the previous run stopped. Every state-mutating operation should answer: "What happens if this runs twice? What happens if the previous run crashed halfway?"

**Why:** Commands, lifecycle operations, and processing loops run where crashes, restarts, and retries are normal. If partial state changes the next run's outcome, every restart becomes a debugging session.

**Derive each step from the state that exists,** not from what the previous run meant to do. Read the current state, compare it to the goal, and apply only the difference. A step written this way is safe to repeat, because a repeat finds nothing left to do.

**Leftovers must be recognizable as stale.** Anything a run leaves behind (a lock, a temporary file, a half-written record, a claimed job) must be judgeable by the next run. Judge staleness by evidence that holds wherever the previous run could have stopped, not only in the common case: a lock names an owner whose liveness the next run can check, a write lands at a temporary path and is renamed into place, artifacts are compared by content rather than by creation order. A marker the next run cannot judge turns every crash into manual cleanup.

**The test:**
1. What happens if this runs twice in a row?
2. What happens if the previous run crashed at every possible point?
3. Does re-execution converge to the same end state?

If any answer is "it depends on what state was left behind," the operation needs a reconciliation step.
