---
name: night
description: Operate night mode - /night on|off|status|preflight|review. "on" runs the three-verdict preflight, mints tonight's GitHub lease with one device-flow click, shows the server-enforced allowlist for confirmation, then flips the machine-wide flag - every Claude session is governed the moment it exists. "review" is the morning procedure. Use when the user wants work to continue while away or asleep - 夜间任务, 无人值守, 睡前布置, 挂一晚上跑, overnight run, nightly run.
---

# /night — night mode operations

Night mode's entire state is one byte: `~/.claude/night/active.json`. Three
channels enforce it, all hot the moment the flag exists — the night-gate hook
(authority), the zshenv hygiene env (every Claude shell, transitive to every
descendant process), and the fnox night profile (credentials, per invocation
via `~/.claude/night/bin/night`). **There is no special night session: any
session while the flag is on IS a night session.**

## on

1. Run `~/.claude/hooks/night-gate.sh preflight`. Three verdicts:
   - **FAILED** — say so and stop. The fabric is broken; a dead channel found
     now costs a click, found at 2am it costs the shift.
   - **DEGRADED** — only the GitHub lease is missing: offer to mint (step 2),
     or with the owner's explicit ok arm local-only (step 6, `--mode local-only`).
   - **PASS** — continue.
2. Mint if needed: run
   `fnox -c ~/.config/fnox/config.toml -P night lease create github -l "night-$(date +%F)"`
   and relay the device code, then confirm with
   `~/.claude/night/bin/night gh api user -q .login`. **The owner's one click
   IS tonight's authorization** — the token lives ~8h, nothing on disk can
   renew it, and the label makes the lease ledger an audit trail.
3. Show the scope preflight printed (the night App's installation
   repositories — the server-enforced push allowlist) and have the owner
   confirm it. Last chance to catch an over-wide grant before sleep.
4. Confirm the shift's task list (Linear). If none was agreed, ask now.
5. Ask about background processes started today and still running — they keep
   their day environment (and anything inherited from an fnox-activated
   launch shell) all night; process memory is the one boundary no channel
   reaches. List survivors in the shift report.
6. Flip: `~/.claude/hooks/night-gate.sh on --mode full --scope repo1,repo2`
   (scope = the list confirmed in step 3; an arm-time record, not truth).
   Flipping also clears the fnox daemon — no day secret survives in memory
   across the boundary — and live sessions get a night notice on their next
   prompt.
7. Suggest a fresh plain `claude` session for the main overnight work — fresh
   context for a long shift; nothing special to launch.

## off

Run `~/.claude/hooks/night-gate.sh off`. If it reports queued entries, offer
`/night review`.

## status

Run `~/.claude/hooks/night-gate.sh status` and relay.

## preflight

Run it and relay verbatim; then check what the script cannot: the Linear MCP
answers, and the shift's task list is non-empty and unambiguous.

## review (morning, owner present, after /night off)

1. Read the newest report in `~/.claude/night/reports/` and summarize:
   completed / queued / blocked / proposals / incidents.
2. **Queue walk** — for each entry in `~/.claude/night/queue/<date>/`, oldest
   first: show the rendered body and the command, then apply the owner's
   verdict: approve → execute the command verbatim from the entry's directory
   (day credentials; the 1Password plugin will Touch-ID each outbound act —
   one presence confirmation per piece of speech); edit → apply, re-show,
   execute; reject → skip. Move processed entries to `done/` (rejected:
   `rejected/`). Never execute an entry that was not approved this morning.
3. **Re-sign & push** — for each completed branch in the report, run its
   ready-to-run block: the rebase --exec re-sign, then
   `git push --force-with-lease` — a branch pushed at night has rewritten
   history after re-signing and will never fast-forward. Verify afterwards:
   `git log --show-signature -1` shows the owner's key.
4. Post the report (with morning outcomes appended) as a comment on the
   shift's Linear issue if the night session did not already.
5. T3 proposals: read them out; execution is the owner's, by hand.
6. Housekeeping: `fnox -c ~/.config/fnox/config.toml -P night lease cleanup`
   (drop expired lease ledger entries).
