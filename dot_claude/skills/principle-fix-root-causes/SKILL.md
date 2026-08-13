---
name: principle-fix-root-causes
description: "Verification. Apply when debugging or when blocked by third-party behavior. Reproduce first, ask why until you reach the root cause, fix it there; no expedient hacks (guards that silence, sleeps that mask, handlers that swallow); escalate third-party fixes as config → wrapper → fork."
---

# Fix root causes

When debugging, do not paper over symptoms. Trace every problem to its root cause and fix it there.

**Why:** Symptom fixes accumulate. Each workaround makes the system harder to reason about, and the real bug remains. Root-cause fixes are slower upfront but reduce total debugging time.

**Pattern:**
- Reproduce first (if you can't reproduce it, you can't verify your fix)
- Ask "why" until you hit the root cause
- Resist the urge to add guards (adding a nil check to silence a crash is a symptom fix)
- If a workaround needs a paragraph-long comment to justify it, the code is wrong (fix the code, not the comment)
- Check for the pattern, not just the instance (grep for the same pattern, fix all instances)
- When stuck, instrument. Don't guess (add logging, read the actual error)

**Restart bugs: suspect state before code.** Code doesn't change between runs. State does. When something "fails after restart," suspect stale persistent state first: config files, caches, lock files, serialized state. If clearing a state file restores behavior, prioritize state validation as the fix.

**When blocked, no expedient hacks.** Do not monkey-patch, sleep to mask races, broaden exception handlers, weaken assertions, or special-case test inputs to force a pass. Fix the root cause; if you can't, stop and report the blocker.

**Third-party root causes.** For third-party behavior that must change, escalate: official config/extension point → subclass or wrapper → fork (recorded in the manifest, per **principle-reproducible-state**). Runtime patching only in tests (`mock.patch`, scoped to the test).
