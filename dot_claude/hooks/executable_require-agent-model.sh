#!/bin/bash
# PreToolUse gate for Agent/Task: require an explicit `model`, or a subagent type
# whose definition pins one. Which model is right is the caller's call; the deny message
# carries the routing table (agent-routing.txt, rendered at apply) so it gets made.
#
# The definition check may be stricter than Claude Code's own agent loader, never
# looser: anything this parser cannot prove pinned is denied.

routing() {
    cat "$HOME/.claude/hooks/agent-routing.txt" 2>/dev/null ||
        printf '%s' "(missing: ~/.claude/hooks/agent-routing.txt; chezmoi apply renders it)"
}

deny() {
    local reason
    reason="Agent call rejected: $1, so it would silently inherit the main-loop model. Re-issue with 'model' set by the class of work, or use an agent type whose .claude/agents/<type>.md frontmatter pins a model. Routing table: $(routing)"
    if command -v jq >/dev/null; then
        jq -cn --arg reason "$reason" \
            '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    else
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Agent call rejected: jq is unavailable, so require-agent-model.sh cannot check the call."}}'
    fi
    exit 0
}

command -v jq >/dev/null || deny "jq is unavailable"

input=$(cat)
jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" || deny "the hook input is not a JSON object"

tool=$(jq -r '.tool_name | strings' <<<"$input")
case "$tool" in
Agent | Task) ;;
*) exit 0 ;;
esac

model=$(jq -r '.tool_input.model | strings | select(test("\\S"))' <<<"$input")
if [[ -n "$model" ]]; then
    exit 0
fi

atype=$(jq -r '.tool_input.subagent_type | strings' <<<"$input")
cwd=$(jq -r '.cwd | strings' <<<"$input")

# `fork` inherits by design; the other two are built-in types with a pinned model
# and no file under .claude/agents/.
case "$atype" in
fork | claude-code-guide | statusline-setup) exit 0 ;;
esac

[[ -n "$atype" ]] || deny "no subagent_type was given"
# The name becomes a path component, so only plain names can resolve to a file.
[[ "$atype" =~ ^[A-Za-z0-9_-]+$ ]] || deny "subagent_type '$atype' is not a local agent name"

MAX_LINES=200

# Fills FM with the frontmatter lines of $1 (CR stripped), or returns 1. Well-formed
# means: line 1 is exactly `---`, a later line within MAX_LINES is exactly `---`, and
# no line between contains `---` (Claude Code closes the block at the first `---`
# anywhere, even mid-line).
read_frontmatter() {
    local line n=0
    FM=()
    while ((n < MAX_LINES)) && { IFS= read -r line || [[ -n "$line" ]]; }; do
        line=${line%$'\r'}
        n=$((n + 1))
        if ((n == 1)); then
            [[ "$line" == "---" ]] || return 1
            continue
        fi
        [[ "$line" == "---" ]] && return 0
        [[ "$line" == *---* ]] && return 1
        FM[${#FM[@]}]=$line
    done <"$1"
    return 1
}

# Prints the value of top-level key $1 in FM when it is spelled exactly once, as
# `<key>: <plain token>`, with no continuation line; returns 1 otherwise. Any other
# line whose key is $1 in some spelling (quoted, `key :`, `? key`) counts as a second
# definition, and a quoted key carrying an escape could be $1, so it counts too.
fm_scalar() {
    local key=$1 i line value="" count=0
    local spelled="^(\\?[[:blank:]]*)?[\"']?${key}[\"']?[[:blank:]]*(:|\$)"
    local escaped="^(\\?[[:blank:]]*)?[\"'][^\"']*\\\\"
    local plain="^$key:[[:blank:]]+([A-Za-z][][A-Za-z0-9._-]*)[[:blank:]]*\$"
    for ((i = 0; i < ${#FM[@]}; i++)); do
        line=${FM[i]}
        if [[ "$line" =~ $spelled || "$line" =~ $escaped ]]; then
            count=$((count + 1))
            [[ "$line" =~ $plain ]] || return 1
            value=${BASH_REMATCH[1]}
            [[ "${FM[i + 1]}" =~ ^[[:space:]] ]] && return 1
        fi
    done
    ((count == 1)) || return 1
    printf '%s' "$value"
}

# Prints the files in directory $1 that could define agent $atype for Claude Code:
# the exact-case `<type>.md` entry, and any *.md whose first MAX_LINES lines set
# `name` to $atype in any spelling (or to a value with an escape that might be it).
# Entries come from the glob, so the comparison sees on-disk case even on a
# case-insensitive filesystem.
claimants() {
    local f line value n
    local name_line="^(\\?[[:blank:]]*)?[\"']?name[\"']?[[:blank:]]*:(.*)\$"
    for f in "$1"/*.md; do
        [[ -f "$f" ]] || continue
        if [[ "${f##*/}" == "$atype.md" ]]; then
            printf '%s\n' "$f"
            continue
        fi
        n=0
        while ((n < MAX_LINES)) && { IFS= read -r line || [[ -n "$line" ]]; }; do
            n=$((n + 1))
            [[ "$line" =~ $name_line ]] || continue
            value=${BASH_REMATCH[2]%$'\r'}
            value=${value%%#*}
            value=${value//[[:space:]\"\']/}
            if [[ "$value" == "$atype" || "$value" == *\\* ]]; then
                printf '%s\n' "$f"
                break
            fi
        done <"$f"
    done
}

# Project agents take precedence over user agents.
def=""
for dir in ${cwd:+"$cwd/.claude/agents"} "$HOME/.claude/agents"; do
    found=$(claimants "$dir")
    [[ -z "$found" ]] && continue
    [[ "$found" == "$dir/$atype.md" ]] ||
        deny "subagent_type '$atype' is not defined by exactly $dir/$atype.md (claimed by: ${found//$'\n'/, })"
    def=$found
    break
done
[[ -n "$def" ]] || deny "subagent_type '$atype' has no agent definition at .claude/agents/$atype.md"

read_frontmatter "$def" || deny "$def has malformed frontmatter"
name=$(fm_scalar name) && [[ "$name" == "$atype" ]] ||
    deny "$def does not declare exactly 'name: $atype'"
pinned=$(fm_scalar model) || deny "$def does not pin a model"
# A plain token starting with a letter is a YAML string unless it is one of these
# words (null/bool in YAML 1.1 or 1.2); `inherit` is Claude Code's own opt-out.
case "$(tr '[:upper:]' '[:lower:]' <<<"$pinned")" in
inherit | null | true | false | yes | no | on | off | y | n)
    deny "$def does not pin a model"
    ;;
esac
exit 0
