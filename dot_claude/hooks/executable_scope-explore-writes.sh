#!/bin/bash
# PreToolUse gate for Write/Edit/NotebookEdit inside the `explore` subagent: the only
# writable location is its own agent-memory directory. `memory: project` needs Write
# and Edit available (the injected memory instructions use the Write tool), so the
# read-only-except-memory contract cannot live in disallowedTools; it lives here.

input=$(cat)

tool=$(jq -r '.tool_name // empty' <<<"$input")
case "$tool" in
Write | Edit | NotebookEdit) ;;
*) exit 0 ;;
esac

# agent_type is present only when the call originates inside a subagent.
atype=$(jq -r '.agent_type // empty' <<<"$input")
[[ "$atype" == "explore" ]] || exit 0

path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$input")
case "$path" in
*..*) ;;                                   # no traversal into or out of the memory dir
/*/.claude/agent-memory/explore/*) exit 0 ;; # user scope (~/.claude) and project scope (<project>/.claude)
esac

reason="explore is read-only outside its agent-memory directory; write refused: ${path:-<none>}. Record memory under .claude/agent-memory/explore/ only."
jq -cn --arg reason "$reason" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
