#!/bin/sh
# Claude Code subagentStatusLine renderer: default row + model display name in
# the right-hand stats cluster ("Haiku 4.5 · 6s · ↓ 15.6k tokens (8%)").
# Usage: render.sh [-- INNER_CMD...]
#   INNER_CMD receives the same stdin (columns shrunk by the model-suffix width);
#   its {"id","content"} lines are kept as row bodies with " · <model>" appended.
#   Ids it omits, or any failure in it, fall back to the default row replica.
# Env: SG_NOW=<epoch seconds> pins "now" (tests).
# Source of truth: developed and golden-tested in ~/Fish/sg-status-line.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
input=$(cat)
[ -n "$input" ] || exit 0

# {id: agentType} from <transcript dir>/subagents/agent-<id>.meta.json.
# Ids are whitelisted before being spliced into JSON.
types=$(printf '%s' "$input" |
    jq -r '(.transcript_path // "" | rtrimstr(".jsonl")) as $d
           | .tasks[]? | select((.id | type) == "string" and (.id | test("^[A-Za-z0-9_-]+$")))
           | .id, "\($d)/subagents/agent-\(.id).meta.json"' |
    while IFS= read -r id && IFS= read -r f; do
        printf '{"id":"%s","meta":' "$id"
        if [ -f "$f" ]; then cat "$f"; else printf 'null'; fi
        printf '}\n'
    done |
    jq -s -c 'map({key: .id, value: (.meta | if type == "object" then .agentType else null end)}) | from_entries')

base='{}'
if [ "${1:-}" = "--" ]; then
    shift
    inner_out=$(printf '%s' "$input" | jq -c -L "$here" 'include "render"; shrink_columns' | sh -c "$*" 2>/dev/null || true)
    base=$(printf '%s' "$inner_out" |
        jq -s -c 'map(select(type == "object" and (.id | type) == "string" and (.content | type) == "string"))
                | map({key: .id, value: .content}) | from_entries' 2>/dev/null || printf '{}')
fi

now=${SG_NOW:-$(date +%s)}
printf '%s' "$input" |
    jq -c -L "$here" --argjson types "$types" --argjson base "$base" --argjson now "$now" \
        'include "render"; main($types; $base; $now)'
