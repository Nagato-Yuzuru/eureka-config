#!/usr/bin/env bash
# PreToolUse hook: block package managers and interpreters that bypass the chosen
# toolchains in Bash commands — npm/npx (pnpm and deno replace them) and the system
# Python tools (uv replaces them).
# Reads tool input from stdin (JSON with tool_name and tool_input). Matching uses only
# bash and jq, so no missing tool can turn a match into a silent pass; without jq it
# blocks rather than guess.

set -uo pipefail

if ! command -v jq >/dev/null; then
    echo "BLOCKED: jq is unavailable, so block-package-managers.sh cannot check this command." >&2
    exit 2
fi

INPUT=$(cat)
[[ "$(jq -r '.tool_name // empty' <<<"$INPUT")" == "Bash" ]] || exit 0
COMMAND=$(jq -r '.tool_input.command // empty' <<<"$INPUT")

# A blocked name counts only in command position:
# 1. Strip single- and double-quoted strings, then comments, so `echo "use npm"` is ignored.
# 2. Newlines separate commands like `;` does.
# 3. Command position: the start, or after ; & | ( ` $( — then any run of wrappers
#    (sudo, command, env, exec, time, nohup) and VAR=value assignments, then the name,
#    bare or as a path, ending the word.
SANITIZED=$(sed "s/'[^']*'//g; s/\"[^\"]*\"//g; s/#.*//" <<<"$COMMAND")
SANITIZED=${SANITIZED//$'\n'/;}
# shellcheck disable=SC2016 # a literal backtick: it opens a command substitution
start='(^|[;&|(`])[[:space:]]*'
wrappers='((sudo|command|env|exec|time|nohup)[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
# shellcheck disable=SC2016 # a literal backtick: it opens a command substitution
path_prefix='([^[:space:];&|()`]*/)?'
# shellcheck disable=SC2016 # a literal backtick: it opens a command substitution
word_end='([[:space:];&|)`]|$)'

in_command_position() {
    [[ "$SANITIZED" =~ $start$wrappers$path_prefix($1)$word_end ]]
}

if in_command_position 'np[mx]'; then
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

if in_command_position 'python[0-9.]*|pip[0-9.]*|virtualenv|poetry'; then
    cat >&2 <<'EOF'
BLOCKED: Do not run python, pip, virtualenv, or poetry directly; everything goes through uv.

Use instead:
  python X.py          → uv run X.py
  python -c / REPL     → uv run python ...
  pip install X        → uv add X  (uv add --dev X for dev deps)
  virtualenv / venv    → uv sync
  one-off tool         → uvx X

This is enforced by user policy. See ~/.claude/CLAUDE.md for details.
EOF
    exit 2
fi

exit 0
