---
name: principle-comments-and-docs
description: "Code & delivery. Apply when writing comments, doc comments, or committed docs. Comments carry why and contract, never what; doc comments are the caller's contract; change code and comment in the same edit; no commented-out code or phase-narrating comments."
---

# Comments & documentation

Governs committed, shared code; the local working tree is your own.

- **Comments carry _why_ and contract, never _what_** — if a comment explains what code does, prefer clearer code; keep it only for irreducible complexity, and say why the odd form is necessary.
- **Doc comments are the caller's contract**: signature + doc must suffice without reading the body — units/timezone, lifecycle, thread-safety, what it throws, side effects.
- **Change the code → change the comment in the same edit**; a comment you can't keep true gets deleted.
- **No commented-out code or inline changelogs** — git remembers (`blame`, `log -L/-S`). If the absence is non-obvious, leave a `NOTE` with the why.
- **Actionable debt gets a tagged owner or ticket**: `TODO(owner|#123)`. Long-lived work goes in the tracker, not buried prose.
- **No phase-narrating comments.** The recurring case is a verify or test script narrating its steps — a `# Phase 1: add cards` line above the block. Delete it; the assertion or log string is the only doc needed. Write `assert ok, 'persisted across restart'`, not a comment plus the code. Write comments clean as you go — a cleanup pass afterward reliably misses them.
