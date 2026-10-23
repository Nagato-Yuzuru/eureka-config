---
name: blast-radius
description: "Find what a change could break somewhere else before it ships, beyond the diff, and prove the one fact it's safe because of by running real code instead of writing it up. Use for 'blast radius of X', 'what could this break', or reviewing a small diff you don't trust."
disable-model-invocation: true
---

# Blast radius

Find what a change breaks somewhere else, before it ships. Use for "blast radius of X", "what could this break", or reviewing a small diff you don't trust yet.

The `explore` agent's explain mode tells you what the code does. The code's history tells you why it's shaped that way, and step 1 reads it. Blast radius tells you what it breaks somewhere else.

Listing the callers is not the job. The agent can grep those in a second. The job is the breakage grep won't show you.

## Don't trust your own writeup

A blast-radius writeup that sounds right is worthless. It reads as convincing whether or not it's true. So don't hand back the writeup. Find the one or two facts the whole thing depends on and prove them by running code.

### How sure are you

For each fact the change's safety depends on, get it as far down this list as is cheap, and say where it stopped.

1. You said so. Worthless on its own.
2. You pointed at the line. A real `file:line`, or the library's own source.
3. You showed the bad case can't happen. You walked the failure step by step and it doesn't reach.
4. You ran it. A script or test that calls the real code and fails loud if you're wrong.
5. You reproduced it in the running app.

Step 4 is usually one small script that imports the same library the app ships and calls the exact function you're worried about.

## Steps

1. Read the change. The diff, the symbols it adds, changes, and deletes, and what it now does differently, including the part the diff doesn't spell out. Then find out what the touched code promises, from its history. A guard, a retry, an odd ordering, or a special case usually exists because something broke without it, and the commit or review that added it says what. Run `git blame` on the touched lines and `git log -L` on the touched functions, open the PRs those commits came from (`gh pr view <n> --json body,comments,reviews`), and follow any issue or incident they link. Each promise you find is a risk to check: the change may remove it, reorder it, or route around it.
2. Find the one fact it's safe because of. Most changes that look risky are safe because of a single fact, like "this call only drops already-dead cache entries and does nothing else". Find that fact. If it holds, most risky cases are cleared at once. Spend your time here, not on a long list of maybes.
3. Look where grep stops. Read the source of the library you call, and check its pinned version and any local patch. Follow what a symbol search misses: the JSON an API returns, a DB column, a wire format, another language reading the same bytes, a feature flag, code three hops downstream.

   Work out when, how often, and in which process the changed code runs. Grep shows who calls it, not when it runs:
   - A UI framework decides when your code runs. React re-runs a component on every render and runs effects after the DOM updates. Solid runs a component once and updates the DOM through signals. The same line can read fresh state in one and stale state in the other.
   - Async code runs later than it reads. An `await` continuation runs after the current synchronous code finishes. A Rust future does nothing until something awaits it.
   - Cleanup runs at the end of a scope, and the scope can be bigger than it looks. An unmount or teardown handler can run after the state it uses is gone. A Rust lock guard lives until the end of its statement, so a `match` on a locked value holds the lock for the whole `match`. A Go `defer` waits for the function to return, not for the loop iteration.
   - A shell pipeline stage runs in a subshell, so variables it sets are gone when the pipeline ends.
4. Be honest about each risk. Give it a real chance of happening and a real cost if it does. Keep the risks you confirmed. List the ones you checked and cleared separately. Cite a real `file:line`, a search that finds nothing is still an answer, and never make up a caller or an API.
5. Prove the one fact. Write a script or test that runs the real code, run it, and paste what happened. When the proof needs its own harness or fixtures, hand the fact to the `oracle` agent as a claim.
6. For a big or wide change, get more reads that aren't anchored on yours. Spawn two or three fresh agents in one message, never forks. Give each the diff and steps 2 to 5 of this skill, but not your writeup: an agent handed your conclusion tends to confirm it. Give each a different lens: who consumes what the change outputs (callers, data formats, other languages), when the changed code runs (lifecycle, concurrency, teardown), or what it assumes about its dependencies (pinned versions, config, environment). Agents with identical briefs share the same blind spots, so the lens is what makes their reads independent. Merge their findings into yours. A risk that two reads found independently is the strongest signal. A risk that only one read found still needs its own proof before it goes in the writeup.

## What to hand back

- **What it does.** What changed, including the part that isn't obvious.
- **The one fact it's safe because of.** State it, say which step you got it to, and show the proof. If you couldn't prove it, write unproven.
- **Risks.** Each names how it breaks, the `file:line`, how likely and how bad, and how to check. Paste the proof for the ones that matter.
- **Cleared.** What you checked and why it's fine.
- **Before you merge.** The cheapest test or repro that catches the real bug, including the script you wrote.

## Writing it

The reader decides whether to merge from the writeup alone.

- Name the mechanism, not the impression. "Deleting the key makes `load()` return the default, and the next `sync` overwrites the user's value" tells the reader what happens. "This could cause data issues" does not.
- Put the uncertainty in each risk's likelihood and cost. Don't spread "might potentially" through every sentence.
- Write whole sentences, one idea each. Spell out arrows and abbreviations. Split any sentence the reader has to read twice.
- Use the plain word: "use", not "leverage"; "breaks", not "impacts".
- Cite real code, a `file:line` or a commit, never a guess at what the code probably does.
- Write in the user's language, and keep code identifiers as they are.
- Strip anything private before it goes anywhere public.

**Reply:** the writeup above, with the one safety fact either proven or marked unproven.
