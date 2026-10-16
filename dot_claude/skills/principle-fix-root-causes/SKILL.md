---
name: principle-fix-root-causes
description: "Verification. Apply when debugging or when blocked by third-party behavior."
---

# Fix root causes

When debugging, fix the fault, not the place where it surfaced. The symptom is where a wrong state became visible. The root cause is the first place the system did something it should not do, or failed to handle a state it should handle.

**Why:** A symptom fix hides the evidence and leaves the fault free to surface somewhere else. Each one makes the system harder to reason about, because the code now carries a workaround for a bug nobody named.

**Pattern:**
- Reproduce first (if you can't reproduce it, you can't verify your fix).
- Ask "why" at each step until the answer is a wrong decision in the code, not a consequence of an earlier one.
- When stuck, instrument. Read the actual error and the actual state; don't guess.
- **Ask whether the triggering state is legitimate.** If the system may be in that state, the code that broke on it is the root cause, and handling the state there is the fix. If the state should never exist, a check where it broke hides the bug: find what produced the state. Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide one.
- Check for the pattern, not just the instance: find every place with the same fault and fix them all.
- If a workaround needs a paragraph-long comment to justify it, the code is wrong. Fix the code, not the comment.
- If the deciding fact took a deep dig to find, propose the cheap, always-on signal (a log line, a metric, a status field, an assertion) that would have shown it at first look.

**Restart bugs: suspect state before code.** Code doesn't change between runs. State does. When something "fails after restart," suspect stale persistent state first: config files, caches, lock files, serialized state. If clearing a state file restores behavior, prioritize state validation as the fix.

**Two failed fixes on one premise: test the premise.** When two or more fixes that share an assumption have failed the same check, the assumption is the suspect, not the fixes. Write it down as one sentence, then make an observation that would come out differently if it were false: look at how the failures are distributed, not only how large they are. If the evidence contradicts it, the cause is elsewhere; look for what the failures share that the premise did not predict. If it confirms it, ask the next "why": the earlier fixes treated a consequence. Don't start the next fix before the premise is written down and tested.

**When blocked, no expedient hacks.** Do not monkey-patch, sleep to mask races, broaden exception handlers, weaken assertions, or special-case test inputs to force a pass. Fix the root cause; if you can't, stop and report the blocker.

**Third-party root causes.** For third-party behavior that must change, escalate: official config/extension point → subclass or wrapper → fork (recorded in the manifest, per **principle-reproducible-state**). Runtime patching only in tests (`mock.patch`, scoped to the test).
