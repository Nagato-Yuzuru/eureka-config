#!/bin/bash
# PreToolUse gate for Write/Edit, registered in the explore agent's own frontmatter so it
# runs only inside explore: the only writable locations are explore's agent-memory
# directories, under $HOME/.claude and $CLAUDE_PROJECT_DIR/.claude. `memory: project` needs
# Write and Edit available (the injected memory instructions use the Write tool), so the
# read-only-except-memory contract cannot live in disallowedTools; it lives here.

deny() {
    jq -cn --arg reason "$1" \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
}

if ! command -v jq >/dev/null; then
    echo "BLOCKED: jq is unavailable, so scope-explore-writes.sh cannot check this write." >&2
    exit 2
fi

input=$(cat)
tool=$(jq -r 'objects | .tool_name // empty' <<<"$input" 2>/dev/null) ||
    deny "scope-explore-writes.sh could not parse its input"
case "$tool" in
Write | Edit) ;;
"") deny "scope-explore-writes.sh could not parse its input" ;;
*) exit 0 ;;
esac

path=$(jq -r '.tool_input.file_path | strings' <<<"$input")
dirs=()
for root in "${HOME:-}" "${CLAUDE_PROJECT_DIR:-}"; do
    [[ "$root" == /?* ]] && dirs+=("$root/.claude/agent-memory/explore/")
done

case "$path" in
*..*) ;; # no traversal into or out of the memory dir
*)
    for dir in ${dirs[@]+"${dirs[@]}"}; do
        [[ "$path" == "$dir"?* ]] && exit 0
    done
    ;;
esac

deny "explore is read-only outside its agent-memory directory; write refused: ${path:-<none>}. Record memory under ${dirs[*]:-<no absolute HOME or CLAUDE_PROJECT_DIR>} only."
