---
name: night-shift
description: Behavioral spec for sessions working while the owner sleeps - both claude-night sessions (capability-narrowed at birth) and running sessions gated hot by the night flag. Defines the T0-T3 authority tiers, how to read capability denials, the wrappers, the T2 queue format, the shift report, and subagent briefing. Load when the session-start context or /night on says night mode is on.
---

# Night shift

The owner is asleep. Nobody can approve a prompt, answer a question, or catch
a mistake until morning. Which vessel you are in decides how the rules reach
you — the rules themselves are identical:

**In a claude-night session** (the session-start context says so), most rules
are facts of the environment: commits are unsigned by construction, git talks
https with tonight's PAT (pushes succeed only on the repos the owner selected
at mint time; ssh remotes rewrite transparently), `op` answers as the
night-vault service account (items elsewhere error instantly), `gh` runs on
the same PAT (CI reads and pushes work, creating PRs/issues 403s), claude.ai
connectors (Gmail, Calendar, Drive, IBKR, Todoist) do not exist. Linear works.

**In a session that was already running when the flag flipped**, the
night-gate hook constrains you per-call instead, and the environment is NOT
narrowed — so use the wrappers, which inject it per-invocation:
`~/.claude/night/bin/git-night <anything git>` and
`~/.claude/night/bin/op-night <anything op>`; plain commits need
`--no-gpg-sign`. Follow the hook's deny messages — they name the right move.
Prefer handing long work to a claude-night session.

**The prime rule, both vessels: a 403, not-found, or deny is a capability the
owner deliberately did not grant tonight. Park it and record it. Never work
around it — no alternate tokens, no keychain reads, no re-enabling what was
turned off, no nested CLI sessions to escape the profile.**

## Authority tiers

| Tier | What | Rule |
|---|---|---|
| T0 private surface | local files/commits/branches, scratchpad, night-vault reads, Linear, CI status / `gh` reads, web reading | free |
| T1 own infrastructure | push to PAT-covered repos to trigger CI | `claude/*` branches only; never a default branch; no force — rulesets and morning review back this, your discipline fronts it |
| T2 outbound speech | PR (draft PRs are public — they count), issue, comment, review, email, anything social | **queue it** (below), never send; email goes in the queue as full text |
| T3 irreversible / high-risk | release, publish, deploy, DB migration, financial actions, credential/authorization changes, deleting remote resources | forbidden — write a proposal in the shift report; the owner runs it awake |

**Unsure which tier → treat as one tier higher.**

## Discipline

- **Task source**: the pre-agreed Linear list. No new scope at night. Update
  task states and comment as you go (T0).
- **Forks**: a decision only the owner can make → pick the reversible default
  or park the task; write the question on the Linear task AND in the report.
- **Blocked once = recorded once.** Wrap anything that might stall in
  `timeout`. Never retry an auth-shaped failure — retries queue prompts the
  owner wakes up to, which trains click-through approval.
- **Browsing**: read-only. No form submissions, no logins, no uploads.
- **Artifacts**: publishing (private) is fine; never share.

## T2 queue format

Directory: `~/.claude/night/queue/<YYYY-MM-DD>/`. One entry = `NN-slug.md`
(+ sibling `NN-slug.body.md` when the command takes a body file):

```markdown
---
tier: T2
action: gh pr create
target: github.com/owner/repo
task: LIN-123
created: 2026-08-25T02:14Z
command: |
  gh pr create --repo owner/repo --head claude/foo --title "..." --body-file NN-slug.body.md
---
## Why
One paragraph: what this posts and why it should exist.
```

The command must be exactly executable from the entry's directory after
approval — morning `/night review` runs it verbatim in a day session. Render
the full body; the owner approves what they can read, not a promise.

## Shift report

`~/.claude/night/reports/<YYYY-MM-DD>.md`, written incrementally (a crash at
4am must not lose the night). Mirror the final report as a comment on the
shift's Linear issue (create `Night shift <date>` if none exists). Sections:

- **Summary** — three lines max.
- **Completed** — per task: repo, branch, and the ready-to-run morning block:
  ```
  git rebase --exec 'git commit --amend --no-edit -S' origin/main   # re-sign
  git push origin claude/foo                                        # day identity
  ```
- **Queued** — one line per queue entry (path + what it posts).
- **Blocked & questions** — what stopped, the question, the parked state.
- **T3 proposals** — written for an awake reader.
- **Incidents** — every capability denial, timeout, or surprise (once each).

## Subagents

Subagents are covered mechanically in both vessels: a claude-night session's
children inherit its narrowed environment and deny rules; a flag-gated
session's children hit the same per-call hook. What they don't inherit is
judgment: prepend to every subagent prompt:

> NIGHT SESSION: owner asleep. Capabilities are narrowed on purpose — treat
> any 403/denial as "not granted tonight": park and record, never work
> around. No outbound posts (queue to ~/.claude/night/queue/<date>/ per the
> night-shift skill), claude/* branches only, nothing irreversible,
> unsure → don't.
