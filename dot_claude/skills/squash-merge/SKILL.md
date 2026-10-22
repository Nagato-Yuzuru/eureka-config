---
name: squash-merge
description: Write the commit message for a squash merge of a pull or merge request with several commits, and merge it. Use when you squash-merge such a request or draft its squash message.
---

# Squash merge

A request is the smallest unit a reader of the default branch sees. Its
squash commit is what that reader finds through `git blame` or `bisect`,
maybe years later, on another host, without the request. So the message
records the **net change** and stands alone: what the branch gets and why.
The path inside the request stays out.

## The message

Each piece of information has one place:

- why the code has this shape → a code comment at the line;
- why the change exists, what behaves differently, what breaks → the message;
- discussion, iterations, trade-offs → the request, reached by its number.

**Subject**: the change in behavior, not in implementation.

**Body**: optional, usually one to three sentences. Keep a sentence only if
a reader without it would misunderstand the change or break the code:

- the reason, when the subject does not make it obvious;
- a breaking change and what users must do;
- an external cause (an upstream bug), with its link;
- if the discussion changed the outcome, the decisive reason in one
  sentence ("Use X, not Y: Y breaks Z.").

Everything else stays out: commit sequence, review rounds, fixes to code
the request added, file lists, restated code, test results, plans, links a
future reader cannot open. A long body means the request holds more than
one change.

## Steps

1. **Conventions.** Authority order: `CONTRIBUTING` and the instructions in
   context; then the last 20 commits on the default branch (format, number
   suffix, trailers); then this skill. Note the check that the `commit-msg`
   hook or commit lint runs. If the repository does not squash, stop.
2. **Scope.** If an automation opened the request, find in its config
   whether it merges by itself; if it does, stop. If the request holds
   changes that a reader would revert separately, propose a stack and stop.
3. **Draft** from the commits and the discussion. List the reasons that
   belong in a code comment but have none; do not edit the code.
4. **Check** the draft with the repository's check.
5. **Approve.** Show the message, the head SHA, and the comment list. Wait
   for approval of the exact text.
6. **Merge**: squash, with the approved text as is, only at that head SHA,
   after the required checks. If the head moved, go back to step 3. Match
   the request title to the subject. Without a CLI for the host, give the
   user the text.

For a stack whose tool takes no message per request, merge one request at
a time from the bottom, and sync the stack after each merge.
