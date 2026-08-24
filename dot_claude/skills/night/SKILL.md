---
name: night
description: Operate night mode - /night on|off|status|preflight|review. "on" verifies channels then flips the machine-wide flag (all running sessions gated hot) and offers the claude-night handoff; "off" ends the night; "review" is the morning procedure - walk the T2 queue, re-sign and push night branches, close the shift report.
---

# /night — night mode operations

Night mode has two composing vessels:

- **The flag** (`~/.claude/night/active.json`): machine-wide, hot — the
  permanently-registered night-gate hook wakes up in every running Claude
  Code session the moment it exists. Manual only: it ends when the owner
  runs `/night off`, never on your initiative.
- **claude-night sessions**: capability-narrowed at birth (tonight's PAT,
  night-vault service account, no ssh, connectors off). The hook stands down
  there. This is the recommended vessel for the main overnight work.

## on

1. Run `~/.claude/hooks/night-gate.sh preflight`. **If it fails, say so and
   stop — a dead credential found at 2am wastes the shift.**
2. Confirm the shift's task list (Linear). If none was agreed, ask now —
   last chance before sleep.
3. Run `~/.claude/hooks/night-gate.sh on`. From this moment every running
   session (this one included, unless it is night-born) is gated per-call.
4. Recommend the handoff for the main work: in the terminal, from the
   project directory, `~/.claude/night/bin/claude-night --continue`
   (continues this conversation) or plain `claude-night` (fresh). It mints
   tonight's PAT if none is stored. Sessions left running stay useful
   through the wrappers (`git-night`, `op-night`) — the hook's deny
   messages point to them.

## off

Run `~/.claude/hooks/night-gate.sh off`. If it reports queued entries,
offer `/night review`.

## status

Run `~/.claude/hooks/night-gate.sh status` and relay.

## preflight

Run `~/.claude/hooks/night-gate.sh preflight` and relay. Then what the
script cannot check: the Linear MCP answers, and the shift's task list is
non-empty and unambiguous.

## review (morning, owner present, after /night off)

1. Read the newest shift report in `~/.claude/night/reports/` and summarize:
   completed / queued / blocked / proposals / incidents.
2. **Queue walk** — for each entry in `~/.claude/night/queue/<date>/`, oldest
   first: show the rendered body and the command, then apply the owner's
   verdict: approve → execute the command verbatim from the entry's
   directory; edit → apply, re-show, execute; reject → skip. Move processed
   entries to `done/` (rejected: `rejected/`). Never execute an entry that
   was not explicitly approved this morning.
3. **Re-sign & push** — for each completed branch in the report, run its
   ready-to-run block (rebase --exec re-sign, then a normal day push; each
   may cost one touch approval, batch them). Verify afterwards:
   `git log --show-signature -1` shows the owner's key.
4. Post the report (with morning outcomes appended) as a comment on the
   shift's Linear issue if the night session did not already.
5. T3 proposals: read them out; execution is the owner's, by hand.
