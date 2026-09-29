#!/usr/bin/env bash
# PreToolUse hook: block npm/npx in Bash commands in favor of pnpm/deno.
# Reads tool input from stdin (JSON with tool_name and tool_input). Matching uses only
# bash and jq, so no missing tool can turn a match into a silent pass; without jq it
# blocks rather than guess.

set -uo pipefail

if ! command -v jq >/dev/null; then
    echo "BLOCKED: jq is unavailable, so block-npm.sh cannot check this command." >&2
    exit 2
fi

INPUT=$(cat)
[[ "$(jq -r '.tool_name // empty' <<<"$INPUT")" == "Bash" ]] || exit 0
COMMAND=$(jq -r '.tool_input.command // empty' <<<"$INPUT")

# npm/npx only in command position:
# 1. Strip single- and double-quoted strings, then comments, so `echo "use npm"` is ignored.
# 2. Newlines separate commands like `;` does.
# 3. Command position: the start, or after ; & | ( ` $( — then any run of wrappers
#    (sudo, command, env, exec, time, nohup) and VAR=value assignments, then npm/npx,
#    bare or as a path, ending the word.
SANITIZED=$(sed "s/'[^']*'//g; s/\"[^\"]*\"//g; s/#.*//" <<<"$COMMAND")
SANITIZED=${SANITIZED//$'\n'/;}
# shellcheck disable=SC2016 # a literal backtick: it opens a command substitution
start='(^|[;&|(`])[[:space:]]*'
wrappers='((sudo|command|env|exec|time|nohup)[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
# shellcheck disable=SC2016 # a literal backtick: it opens a command substitution
word='([^[:space:];&|()`]*/)?np[mx]([[:space:];&|)`]|$)'
if [[ "$SANITIZED" =~ $start$wrappers$word ]]; then
    cat >&2 <<'EOF'
BLOCKED: Do not use npm or npx.

Use instead:
  npm install  → pnpm install
  npm run X    → pnpm run X
  npm add X    → pnpm add X
  npx X        → pnpm dlx X  (or  deno run npm:X  for non-interactive tools)

This is enforced by user policy. See ~/.claude/CLAUDE.md for details.
EOF
    exit 2
fi

exit 0
