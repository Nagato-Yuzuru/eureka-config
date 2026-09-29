#!/bin/bash
# PreToolUse gate for Workflow: reject inline scripts that spawn agents with zero
# model routing (no `model:` / `agentType:` token anywhere). This is a heuristic
# textual screen, not a JS parse — it catches only total omission (the observed
# failure mode). Whether the model picked for each agent is the *right* one can't
# be decided mechanically, so this hook doesn't try; the deny message below carries
# the routing table so the choice at least gets made deliberately.

deny() {
  local reason="Workflow script rejected: $1"
  if command -v jq >/dev/null; then
    jq -cn --arg reason "$reason" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
  else
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Workflow script rejected: jq is unavailable, so require-workflow-routing.sh cannot check the call."}}'
  fi
  exit 0
}

command -v jq >/dev/null || deny "jq is unavailable"
input=$(cat)
jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" || deny "the hook input is not a JSON object"

tool=$(jq -r '.tool_name | strings' <<<"$input")
if [[ "$tool" != "Workflow" ]]; then
  exit 0
fi

# Inline script, or a script file (iteration/resume path). Named workflows
# ({name: ...}) are pre-authored and not screened here.
script=$(jq -r '.tool_input.script // empty' <<<"$input")
if [[ -z "$script" ]]; then
  path=$(jq -r '.tool_input.scriptPath // empty' <<<"$input")
  if [[ -n "$path" && -r "$path" ]]; then
    script=$(cat "$path")
  fi
fi
if [[ -z "$script" ]]; then
  exit 0
fi

# No agent() spawns -> nothing to route.
if ! grep -q 'agent(' <<<"$script"; then
  exit 0
fi

if grep -Eq '(model|agentType)[[:space:]]*:' <<<"$script"; then
  exit 0
fi

routing=$(cat "$HOME/.claude/hooks/agent-routing.txt" 2>/dev/null) ||
  routing="(missing: ~/.claude/hooks/agent-routing.txt; chezmoi apply renders it)"
deny "it calls agent() but contains no 'model:' or 'agentType:' routing anywhere, so every agent would silently inherit the main-loop model. Set opts.model on each agent() call by the class of work, or opts.agentType to one of the pinned agents, then re-issue. Routing table: $routing"
