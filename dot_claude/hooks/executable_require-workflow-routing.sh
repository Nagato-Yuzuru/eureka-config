#!/bin/bash
# PreToolUse gate for Workflow: reject inline scripts that spawn agents with zero
# model routing (no `model:` / `agentType:` token anywhere). This is a heuristic
# textual screen, not a JS parse — it catches only total omission (the observed
# failure mode). Whether the model picked for each agent is the *right* one can't
# be decided mechanically, so this hook doesn't try; the deny message below carries
# the routing table so the choice at least gets made deliberately.

input=$(cat)

tool=$(jq -r '.tool_name // empty' <<<"$input")
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

reason="Workflow script rejected: it calls agent() but contains no 'model:' or 'agentType:' routing anywhere, so every agent would silently inherit the main-loop model. Set opts.model on each agent() call per the routing table (web fetch / file search / log scan -> haiku; code reasoning / codegen -> sonnet; multi-file planning / refactoring -> opus; fable never as default), or use opts.agentType with a pinned definition (fetcher=haiku, coder=sonnet, architect=opus, Explore=haiku), then re-issue."

jq -cn --arg reason "$reason" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
