---
name: principle-comments-and-docs
description: "Code & delivery. Apply when writing comments, doc comments, or committed docs. Comments carry why and contract, never what — telegraphic, written for the reader who was never in the room; debt/danger/deviation leads with its tag (TODO/FIXME/HACK/WARN/PERF/NOTE); doc comments are the caller's contract; change code and comment in the same edit; no commented-out code or phase-narrating comments."
---

# Comments & documentation

Governs committed, shared code; the local working tree is your own.

- **Comments carry _why_ and contract, never _what_** — if a comment explains what code does, prefer clearer code; keep it only for irreducible complexity, and say why the odd form is necessary.
- **Telegraphic.** One line is the shape of a comment: subject–verb, no scene-setting, no restating the signature ("This function is responsible for handling…" is the signature, slower). Multi-line survives only when the contract itself is that big — a protocol, an invariant table, units. An explanation that wants a paragraph is asking for a refactor first.
- **Write for the reader who was never in the room.** A comment outlives the conversation that produced it; anything legible only against that conversation — the rejected alternative, the instruction it answers ("use X here, not Y"), the plan's phasing, the feature we might add later — is **transcript**, and transcript gets deleted. A rejected alternative earns a comment only as a guard with its failure mode named (`// linear scan: bisect loses stable order on equal keys`), never as a menu of what this code isn't. Speculative futures live in the tracker or nowhere.
- **Doc comments are the caller's contract**: signature + doc must suffice without reading the body — units/timezone, lifecycle, thread-safety, what it throws, side effects.
- **Change the code → change the comment in the same edit**; a comment you can't keep true gets deleted.
- **No commented-out code or inline changelogs** — git remembers (`blame`, `log -L/-S`). If the absence is non-obvious, leave a `NOTE` with the why.
- **A reference with no novel content gets deleted**, never left as a stub.
- **Debt, danger, and deviation lead with their tag.** The closed set: `TODO(owner|#123)`, `FIXME`, `HACK` (names the root cause it dodges), `WARN`, `PERF`, `NOTE` — one canonical spelling each (`FIXME` over `BUG`, `WARN` over `XXX`), so `grep TODO` stays one pattern. Long-lived work goes in the tracker, not buried prose.
- **No phase-narrating comments.** The recurring case is a verify or test script narrating its steps — a `# Phase 1: add cards` line above the block. Delete it; the assertion or log string is the only doc needed. Write `assert ok, 'persisted across restart'`, not a comment plus the code. Write comments clean as you go — a cleanup pass afterward reliably misses them.
