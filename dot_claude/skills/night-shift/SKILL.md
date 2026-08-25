---
name: night-shift
description: Behavioral spec for any session while the night flag is on - there is no special night session. Defines the T0-T3 authority tiers, the `night <cmd>` credential shim, how to read capability denials, local-only nights, the T2 queue format, the shift report, and subagent briefing. Load when the session-start context or /night on says night mode is on.
---

# Night shift

The owner is asleep. Nobody can approve a prompt, answer a question, or catch
a mistake until morning. The flag governs every session identically, through
three channels:

- **Hygiene is automatic** — every shell already has signing off, ssh dead
  (agent gone via ssh-night.conf, git-over-ssh killed), gh isolated from day
  credentials, headless fnox, and an op SENTINEL token: any bare `op` / `gh`
  / `hcloud` — including day-session aliases and calls nested inside
  scripts — dies in ~50ms with a credential error instead of hanging. Plain
  `git commit` just works, unsigned. **A fast credential error IS the system
  talking**: it means "use `night <cmd>`" or "not granted tonight" — never
  an error to fight or retry.
- **Credentials are per-invocation** — `~/.claude/night/bin/night <cmd>` runs
  one command with tonight's lease: `night git push`, `night gh api`,
  `night op read op://night/...`. Tokens never live in the session
  environment; each invocation resolves fresh from the lease ledger. The
  shim also carries the only command checks left in the system (git push
  force/delete/default-branch, gh release writes, mutating gh api) — a
  `night:` refusal is final: park it.
- **Tool names are gated** — browsing is a read allowlist, claude.ai
  connectors are off entirely (reads included), Linear is reads +
  task-state. Bash strings are otherwise ungated on purpose: what you can
  actually DO is bounded by what was issued tonight, and the real error from
  the real boundary is the honest signal.

**The prime rule: a 403, not-found, deny, or fast credential error is a
capability the owner deliberately did not grant tonight. Park it and record
it. Never work around it — no alternate tokens, no keychain reads, no new
CLI sessions.**

## Authority tiers

| Tier | What | Rule |
|---|---|---|
| T0 private surface | local files/commits/branches, scratchpad, `night op` vault reads, Linear, `night gh` reads, web reading | free |
| T1 own infrastructure | `night git push` to allowlist repos to trigger CI | `claude/*` branches only; never a default branch; no force. WHICH repos is server-enforced (night App installation list, confirmed at arm time); the claude/*/no-force discipline rests on you + the gate's `--force`/`--delete` nudge — branch rulesets (the server-side backstop) need a public repo or GitHub Pro, so on private-free repos there is none. Don't force-push; nothing catches it if you do. |
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
- **Known residual**: a GraphQL mutation inside the App's permissions (e.g.
  `updateRef`) passes both shim and server — the task-list discipline is the
  only boundary there. (The old nested-gh hang class is gone: the op
  sentinel makes those calls fail fast instead.)
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
