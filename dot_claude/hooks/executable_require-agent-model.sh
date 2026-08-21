#!/bin/bash
# PreToolUse gate for Agent/Task: require an explicit `model`, or a subagent type
# whose definition pins one. Mechanizes the model-routing table in ~/.claude/CLAUDE.md.

input=$(cat)

tool=$(jq -r '.tool_name // empty' <<<"$input")
case "$tool" in
Agent | Task) ;;
*) exit 0 ;;
esac

model=$(jq -r '.tool_input.model // empty' <<<"$input")
if [[ -n "$model" ]]; then
    exit 0
fi

# Exempt: `fork` always inherits by design; the rest pin a model in their
# definition (~/.claude/agents/*.md frontmatter, or built-in). Extend this
# list when adding new pinned-model agent definitions.
atype=$(jq -r '.tool_input.subagent_type // empty' <<<"$input")
case "$atype" in
fork | explore | fetcher | coder | architect | linus-reviewer | oracle | claude-code-guide | statusline-setup)
    exit 0
    ;;
esac

reason="Agent call rejected: no explicit 'model' and subagent_type '${atype:-<none>}' has no pinned model, so it would silently inherit the main-loop model. Re-issue with 'model' set per the routing table: web fetch / file search / log scan -> haiku; code reasoning / codegen -> sonnet; multi-file planning / refactoring / cross-file consistency -> opus; fable is never a default (per-case escalation only; requires user approval). Alternatively use a model-pinned agent type: fetcher (haiku), explore (sonnet), coder (sonnet), architect (opus), linus-reviewer (opus), oracle (opus)."

jq -cn --arg reason "$reason" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
