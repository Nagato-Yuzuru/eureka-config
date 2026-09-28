---
name: sweep
description: "Apply when an artifact reaches its final state — pre-commit, pre-publish, or hand-off of a skill, doc, config, or code file — or when the user asks for a residue sweep. The finalization event is the trigger; how clean the output looks is not a signal."
---

# Sweep

Residue — text only the production history can justify: the "(not X)" a "don't X" leaves behind, session labels, iteration narration, one-instance literals in general guidance — is invisible to its author. You wrote the artifact, therefore it looks clean to you, therefore looking clean carries no information. The sweep runs on the finalization event itself, on every terminal artifact, every time.

## 1. Dispatch

Spawn the `sweeper` agent (Agent tool; a fork inherits this session's context and blinds the sweep — always a fresh spawn). The briefing is these four fields, filled and sent verbatim:

```
Sweep as first reader.
Files: <paths>
Type: <skill | doc | code | config | commit message>
Audience: <one line — who reads this cold>
Output dir: <session scratchpad>/sweep/
```

The sweeper judges from what the artifact alone establishes; the four fields are the whole channel. Background, task history, and context that might help stay here.

## 2. Disposition — accept or veto, per patch file

The sweeper returns per-finding patch files plus a report. Two moves per finding:

- **Accept** — the flagged text is residue.
- **Veto** — you hold ground truth the sweeper cannot: the flagged text is a real contract (a spec constraint, a promised behavior). A veto is dropping the patch file, with a one-line reason in your report to the user.

A patch whose text you would write differently is a veto, not an edit — patch prose is composed only on the sweeper's side. Findings marked **referent wanted** (a dereference or define missing its referent): send the referent — the bare fact, one line — back to the sweeper via SendMessage; it revises the patch. Findings marked **question**: resolve against ground truth first, then accept or veto like any other.

## 3. Apply

`git apply` the accepted patch files (the sweeper pre-checks each). Done when every finding is dispositioned: accepted patches applied cleanly, vetoes reported with reasons.
