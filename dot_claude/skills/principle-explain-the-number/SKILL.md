---
name: principle-explain-the-number
description: "Verification. Apply before you trust, report, or act on a number you measured: a speedup, a regression, a throughput, a latency, an eval score."
---

# Explain the number

A measured number is a claim about what limits the system. Until you can name that limit and rule out the other things the number could be measuring, you don't know what you measured.

**Why:** A wrong number looks exactly like a right one. A benchmark that hit a cached path, a load generator that saturated first, or an eval where half the runs errored all produce clean, confident figures, and every decision built on them inherits the error.

**The pattern:**
- **Ask "why not double?"** Name the resource or code path that bounds the result: a core, a lock, the disk, the network, the load generator itself. Get it from a profile or from system counters taken during the run, then map it to source. A guess from reading the code is not a limiter.
- **List what else the number could be measuring, and rule out each with evidence.** The usual suspects: errors, skipped or cached work, code that never ran, an untuned side (a baseline left on default settings), noise, and a piece too small to matter end to end.
- **For an eval result,** ask the same of the trials: did every run actually do the task, does the gap hold across trials and models, does the scenario matter.
- **Keep the evidence with the number.** Run count, spread, and the named limiter go in the notes or a linked artifact, next to the figure.

**The tell you skipped this:** the number has no run count, no spread, or no named limiter, or the time saved exceeds the time the changed piece took in the first place.

Distinct from **principle-prove-it-works**, which checks that an output is real; this checks that a measurement means what you say it means.
