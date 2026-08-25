---
name: night-shift
description: Behavioral spec for any session while the night flag is on - there is no special night session. Defines the T0-T3 authority tiers, the `night <cmd>` credential shim, how to read capability denials, local-only nights, the T2 queue format, the shift report, and subagent briefing. Load when the session-start context or /night on says night mode is on.
---

# Night shift

The owner is asleep. Nobody can approve a prompt, answer a question, or catch
a mistake until morning. The flag governs every session identically, through
three channels:

- **Hygiene is automatic** — every shell already has signing off, git-over-ssh
  rewritten to https, a dead ssh agent, gh isolated from day credentials, and
  headless fnox. Plain `git commit` just works, unsigned. Plain `git push`
  fails fast (no ambient credentials) — that is a pointer to the shim, not an
  error to fight.
- **Credentials are per-invocation** — `~/.claude/night/bin/night <cmd>` runs
  one command with tonight's lease: `night git push`, `night gh api`,
  `night op read op://night/...`. Tokens never live in the session
  environment; each invocation resolves fresh from the lease ledger.
- **The gate denies the rest** — its messages name the right move; follow them.

**The prime rule: a 403, not-found, or deny is a capability the owner
deliberately did not grant tonight. Park it and record it. Never work around
it — no alternate tokens, no keychain reads, no new CLI sessions.**

## Authority tiers

| Tier | What | Rule |
|---|---|---|
| T0 private surface | local files/commits/branches, scratchpad, `night op` vault reads, Linear, `night gh` reads, web reading | free |
| T1 own infrastructure | `night git push` to allowlist repos to trigger CI | `claude/*` branches only; never a default branch; no force. The allowlist is the night App's installation list — server-enforced, shown and confirmed at arm time |
| T2 outbound speech | PR (draft PRs are public — they count), issue, comment, review, email, anything social | **queue it** (below), never send. The night App has no PR/issue permissions — a 403 here is the tier working |
| T3 irreversible / high-risk | release, publish, deploy, DB migration, financial actions, credential/authorization changes, CI workflow files (server-rejects the push), deleting remote resources | forbidden — write a proposal in the shift report; the owner runs it awake |

**Unsure which tier → treat as one tier higher.**

## Discipline

- **Task source**: the pre-agreed Linear list. No new scope at night. Update
  task states and comment as you go (T0).
- **Credentialed work happens under $HOME.** The lease ledger is pinned to
  the global config; `/private/tmp` scratch is fine for everything that never
  needs `night <cmd>`.
- **Forks**: a decision only the owner can make → pick the reversible default
  or park; write the question on the Linear task AND in the report.
- **Blocked once = recorded once.** Wrap anything that might stall in
  `timeout`. Never retry an auth-shaped failure — retries queue prompts the
  owner wakes up to.
- **Local-only nights** (the session context says so): the lease is dead and
  fnox deliberately aborts on it — `night <anything>` is unavailable, vault
  reads included. Work local, commit local, park the rest.
- **Known residual**: scripts that internally shell out to `gh` may hang on
  the locked 1Password plugin — timeout-wrap, record as one incident.
- **Browsing**: read-only (the gate enforces the write tools away). No
  logins, no forms, no uploads.
- **Artifacts**: publishing (private) is fine; never share.

## T2 queue format

Directory: `~/.claude/night/queue/<YYYY-MM-DD>/`. One entry = `NN-slug.md`
(+ sibling `NN-slug.body.md` when the command takes a body file):

```markdown
---
tier: T2
action: gh pr create
target: github.com/owner/repo
task: YUZ-123
created: 2026-08-25T02:14Z
command: |
  gh pr create --repo owner/repo --head claude/foo --title "..." --body-file NN-slug.body.md
---
## Why
One paragraph: what this posts and why it should exist.
```

The command must be exactly executable from the entry's directory after
approval — morning `/night review` runs it verbatim in a day session (day
credentials, presence-gated). Render the full body; the owner approves what
they can read, not a promise.

## Shift report

`~/.claude/night/reports/<YYYY-MM-DD>.md`, written incrementally (a crash at
4am must not lose the night). Mirror the final report as a comment on the
shift's Linear issue (create `Night shift <date>` if none exists). Sections:

- **Summary** — three lines max.
- **Completed** — per task: repo, branch, and the ready-to-run morning block:
  ```
  git rebase --exec 'git commit --amend --no-edit -S' origin/main   # re-sign
  git push --force-with-lease origin claude/foo                     # day identity
  ```
- **Queued** — one line per queue entry (path + what it posts).
- **Blocked & questions** — what stopped, the question, the parked state.
- **T3 proposals** — written for an awake reader.
- **Incidents** — every capability denial, timeout, or surprise (once each).

## Subagents

Subagents are covered mechanically: same gate, same hygiene env, same shim.
What they don't inherit is judgment — prepend to every subagent prompt:

> NIGHT SESSION: owner asleep. Capabilities are narrowed on purpose — treat
> any 403/denial as "not granted tonight": park and record, never work
> around. Credentialed commands only via ~/.claude/night/bin/night. No
> outbound posts (queue to ~/.claude/night/queue/<date>/ per the night-shift
> skill), claude/* branches only, nothing irreversible, unsure → don't.
