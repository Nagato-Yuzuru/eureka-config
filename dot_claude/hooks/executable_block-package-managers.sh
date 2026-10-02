#!/usr/bin/env bash
# PreToolUse hook: block the package managers that bypass the chosen toolchains in Bash
# commands — npm/npx (pnpm and deno replace them) and the Python installers: pip,
# virtualenv, poetry, and python -m pip/venv/ensurepip (uv replaces them). Running python
# itself passes: stdlib code installs nothing, and code that needs a package fails on the
# import, whose fix is an install this hook stops.
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
# 1. Drop the bodies of heredocs with a quoted delimiter (<<'X', <<"X", <<\X): the shell
#    expands nothing in them, so they are text. An unquoted heredoc runs its $(...), so its
#    body is still checked.
# 2. Strip single- and double-quoted strings, then comments, so `echo "use npm"` is ignored.
# 3. Newlines separate commands like `;` does.
# 4. Command position: the start, or after ; & | ( ` $( — then any run of wrappers
#    (sudo, command, env, exec, time, nohup) and VAR=value assignments, then the name,
#    bare or as a path, ending the word.
drop_quoted_heredoc_bodies() {
    # `[^<]` before `<<` keeps a here-string (<<<'x') from opening a heredoc. A body line
    # ends the heredoc when it equals the delimiter after leading tabs (as <<- allows); for
    # a plain << that can only resume checking early, never late.
    local re="(^|[^<])<<-?[[:space:]]*(\\\\|'|\")([A-Za-z_][A-Za-z0-9_]*)"
    local line delim="" tabs
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ -n "$delim" ]]; then
            tabs=${line%%[!$'\t']*}
            [[ "${line#"$tabs"}" == "$delim" ]] && delim=""
            continue
        fi
        printf '%s\n' "$line"
        if [[ "$line" =~ $re ]]; then
            delim=${BASH_REMATCH[3]}
        fi
    done <<<"$1"
}

SANITIZED=$(drop_quoted_heredoc_bodies "$COMMAND" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g; s/#.*//")
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

# python[X.Y] … -m pip|venv|ensurepip, with any options before -m.
python_installs() {
    [[ "$SANITIZED" =~ $start$wrappers${path_prefix}python[0-9.]*[[:space:]]+([^;\&|]*[[:space:]])?-m[[:space:]]*(pip|venv|ensurepip)$word_end ]]
}

if in_command_position 'pip[0-9.]*|virtualenv|poetry' || python_installs; then
    cat >&2 <<'EOF'
BLOCKED: Python packages and environments go through uv, not pip, virtualenv, poetry, or
python -m pip/venv/ensurepip. Plain python3 is fine for stdlib-only code.

Use instead:
  pip install X               → uv add X  (uv add --dev X for dev deps)
  a script that needs X       → uv run --with X script.py, or PEP 723 inline deps + uv run
  python -m venv / virtualenv → uv sync in a project, uv venv otherwise
  one-off tool                → uvx X

This is enforced by user policy. See ~/.claude/CLAUDE.md for details.
EOF
    exit 2
fi

exit 0
