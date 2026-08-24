---
name: orchestration
description: "Process. Apply when structuring work into units or a multi-stage flow: deciding whether to decompose, choosing the shape (chain, fan-out, panel, evaluator loop), slicing into tickets, dispatching and collecting. Reached from the engineering index's Orchestration pointer."
---

# Orchestration

Structure work as a flow whose nodes are mutually ignorant and whose order lives here, in the process. A node — agent, linter, script — is total over its own domain and assumes no other node exists; sequencing, deduplication, and priority are the orchestrator's job. Redundancy between nodes is a process smell, never a node defect.

## First: should this decompose at all?

Five tests. T1/T2 detect where separation is a mechanism; T3–T5 decide whether it pays.

- **T1 · Stance conflict.** The task needs two stances that contaminate each other in one context: find-everything vs judge-calmly, generate vs evaluate, presume guilty vs presume innocent. Separate contexts are then a requirement, not an optimization — no model tier removes the need, because one context cannot hold both incentives honestly.
- **T2 · Fresh eyes.** The consumer must not see the producer's reasoning (a verifier anchors on it; a self-reviewer forgives it) → fresh-context subagent; a fork would inherit the bias. The reverse case: work that needs the conversation itself (decomposing, summarizing, filing what was just decided) belongs to the main loop or a fork, never a fresh subagent.
- **T3 · Economics.** The engineering index's delegation criteria (compression ratio, overhead floor, discardable reads). Failing them, work directly.
- **T4 · Mechanical verdict.** An evaluator loop converges only when acceptance is executable — tests, assertions, lint, ast-grep. Without a mechanizable verdict, use a single review pass plus owner judgment; a loop graded on vibes spins in place.
- **T5 · Independence.** Parallel units share no written state; panels use diverse lenses (identical reviewers vote with identical bias, so their agreement is noise). A shared write target makes parallel the wrong shape regardless of model quality.

## Choose the shape

| Shape | Reach for it when | Backbone |
|---|---|---|
| Chain with gates | Fixed stages; cheap checks shield expensive ones (red CI gets no reviewer) | ticket blocking edges, or a workflow |
| Fan-out | Independent units, T5 clean | one-message batch, or workflow `parallel()` |
| Panel | One artifact, several diverse lenses; disagreement is the signal | fan-out + synthesis |
| Evaluator loop | T4 holds: a builder iterates until the oracle passes | workflow, or a supervised loop |
| Judge panel | Design candidates for a competent adjudicator, each from an isolated context | parallel candidate calls → owner picks |

Shapes compose, and the ticket graph carries all of them: blocking edges encode the chain, blocker-free tickets are the parallel frontier.

**Small-task floor.** When T3 rules out full decomposition — the change fits one file or class — the minimal flow that still honors T1 is one implementer, then linus-reviewer, then oracle post-hoc on the falsifiable findings. Reserve the judge panel for decisions with a genuine second plausible shape (one both candidates would dismiss in five lines is not one), and stop the panel at convergence: two independent candidates agreeing is the answer, not a reason to fund a third.

## Slice and ticket

Slice into **tracer-bullet tickets**: each a vertical cut through every layer that is verifiable on its own, sized so one fresh context window can execute it. Each ticket declares its **blocking edges** — the tickets that must land first; blocker-free tickets form the parallel frontier. Present the breakdown for a granularity-and-edges check, and in the same exchange ask the owner where tickets land this run: a tracker this project owns (Linear, GitHub/GitLab issues on our own repos) or a local file outside version control. Publish one unit per ticket to exactly the destination the owner named. A defect found in a third-party or OSS project becomes an internal ticket and climbs principle-fix-root-causes' escalation ladder; an upstream issue is an outward action the owner approves case by case — duplicate-checked, minimal repro attached — never a slicing output. If the skill listing offers a ticketing capability that implements this, use it — these criteria still govern the result.

The ticket rules:

1. A ticket is a **brief**: a fresh context must be able to execute it without asking anything — scope, context, acceptance criteria, the output contract (a schema when a machine consumes the result), and the model tier when it departs from the executor's pin.
2. Tickets are read by humans first. State the end-to-end behavior from the user's perspective; mechanism over feeling, active voice, no restated headers. Skip file paths and code that go stale; keep only decision-carrying snippets.
3. Record adjudications. When the owner rules on a tradeoff, write the ruling into the ticket or artifact — an undocumented decision invites relitigation from every guilty-presumption reviewer that later reads the code.

Done when every unit's blockers are named, every brief passes the fresh-context test, and every mechanizable acceptance criterion is written into its ticket.

## Dispatch and collect

Read `references/dispatch.md` before sending work out — briefing contract, batching, fork-vs-subagent, collection. Read `references/workflow-templates.md` when the flow deserves a script. The ladder: a Workflow script for control flow that matters, a single-message Agent batch for one-shot fan-outs, model discretion only for cheap exploration.
