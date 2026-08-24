#!/usr/bin/env bash
# night-gate v3 — night-shift gate + support tooling for Claude Code.
#
# Night mode has two vessels that compose:
#
#   1. The machine-wide flag (~/.claude/night/active.json), toggled by on/off.
#      Hot: the PreToolUse hook below is permanently registered and inert
#      until the flag exists, so ALL running sessions are constrained the
#      moment the owner flips it — no restarts. Settings files never change
#      at toggle time (a botched splice would silently kill every setting).
#      Running sessions keep working through per-invocation env wrappers:
#      ~/.claude/night/bin/git-night and op-night.
#
#   2. claude-night sessions (the recommended overnight workhorse, incl.
#      --continue handoff): capabilities narrowed at birth (env + tonight's
#      PAT + SA + connectors off via the settings overlay). They export
#      NIGHT_SESSION=1; the hook sees it (hooks inherit session env) and
#      stands down — enforcement there is capability-grade already.
#
# Enforcement stack, strongest first: capabilities (PAT repo selection, SA
# vault scope, no SSH identities) > GitHub repo rulesets (branch discipline)
# > this hook / overlay deny rules (drift guardrails) > night-shift prose.
#
# Modes:
#   (no args)      PreToolUse gate: inert unless flag on and NIGHT_SESSION!=1
#   on | off       flip the flag (manual, indefinite — owner's call only)
#   status         flag / config / keychain / queue at a glance
#   preflight      verify every night channel; non-zero = don't start a shift
#   session-start  SessionStart context. Global registration: fires when the
#                  flag is on in a NON-narrowed session. --overlay variant
#                  (registered only in the night overlay): fires always.

set -euo pipefail

NIGHT_DIR="$HOME/.claude/night"
CONFIG="$NIGHT_DIR/config.json"
ACTIVE="$NIGHT_DIR/active.json"
QUEUE_DIR="$NIGHT_DIR/queue"

# presence-only flag: manual on/off, no time semantics (owner is a night owl
# and knows when they sleep). A corrupt flag file is still a file: fail-closed.
is_active() { [[ -e "$ACTIVE" ]]; }

cfg() { jq -r --arg d "$2" "$1 // \$d" "$CONFIG" 2> /dev/null || echo "$2"; }

# --- hook mode ---------------------------------------------------------------

# Fail-closed backstop: a crash mid-decision must deny, not ask — at night
# nobody answers an ask, and a deny lets the agent park and move on.
HOOK_DECIDED=0
hook_exit_backstop() {
    if [[ "${HOOK_DECIDED:-0}" != 1 ]]; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"night-gate crashed before reaching a decision; failing closed. Park this action, note it in the shift report, continue with the next task."}}'
    fi
    exit 0
}

emit_deny() { # REASON
    jq -n --arg r "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $r
        }
    }'
    HOOK_DECIDED=1
    exit 0
}

allow() {
    HOOK_DECIDED=1
    exit 0
}

# Regex over command strings is deliberately coarse and fail-closed: a false
# positive costs a rephrase or a queue entry, a false negative costs an
# outbound action under the owner's name. This layer catches drift, not
# adversaries — the capability layer is the real boundary.
gate_bash() { # CMD
    local c="$1"

    # liveness: would hang on the locked 1Password
    if grep -qE '(^|[;&|[:space:](])(ssh|scp|sftp|rsync)[[:space:]]' <<< "$c"; then
        emit_deny "night: the 1Password SSH agent is locked; this would hang until morning. git-over-network goes through ~/.claude/night/bin/git-night; anything else ssh-shaped: park the task and record it in the shift report."
    fi
    if grep -qE '(^|[;&|[:space:](])op[[:space:]]' <<< "$c" && ! grep -q 'OP_SERVICE_ACCOUNT_TOKEN' <<< "$c"; then
        emit_deny "night: interactive op hits the locked desktop app and hangs. Use ~/.claude/night/bin/op-night (same arguments — answers as the night-vault service account; an item not in that vault is unavailable tonight: park it)."
    fi

    # network git: ssh remotes hang; git-night goes https with tonight's PAT
    if grep -qE '(^|[;&|[:space:](])git([[:space:]]+-[Cc][[:space:]]+[^[:space:]]+|[[:space:]]+-c[[:space:]]+[^[:space:]]+)*[[:space:]]+(push|fetch|pull|clone)\b' <<< "$c"; then
        emit_deny "night: raw network git hangs on the locked 1Password agent. Use ~/.claude/night/bin/git-night (same arguments — https + tonight's PAT; pushes succeed only on the repos the PAT was minted for, claude/* branches by convention)."
    fi

    # signing: op-ssh-sign hangs when locked, and a night commit must not
    # carry the owner's attestation anyway. Morning review re-signs.
    if grep -qE '(^|[;&|[:space:](])git([[:space:]]+-[Cc][[:space:]]+[^[:space:]]+|[[:space:]]+-c[[:space:]]+[^[:space:]]+)*[[:space:]]+commit\b' <<< "$c" \
        && ! grep -qE -- '--no-gpg-sign|commit\.gpgsign=false' <<< "$c"; then
        emit_deny "night: signing goes through the locked 1Password (hangs), and night commits are not the owner's attestation. Re-run with --no-gpg-sign (or use git-night); morning review re-signs before push."
    fi

    # T2: outbound speech under the owner's identity -> queue
    if grep -qE '(^|[;&|[:space:](])gh[[:space:]]+(pr|issue)[[:space:]]+(create|comment|close|reopen|merge|edit|review|lock|ready)\b' <<< "$c" \
        || grep -qE '(^|[;&|[:space:](])gh[[:space:]]+gist[[:space:]]+create\b' <<< "$c"; then
        emit_deny "night T2: outbound speech under the owner's account. Write it as a queue entry instead: ~/.claude/night/queue/\$(date +%F)/NN-slug.md (format in the night-shift skill). Morning /night review executes approved entries. Draft PRs are public too — they queue, not post."
    fi
    if grep -qE '(^|[;&|[:space:](])gh[[:space:]]+api\b' <<< "$c" \
        && grep -qiE -- '-X[[:space:]]*(POST|PUT|PATCH|DELETE)|--method[[:space:]=]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])-(f|F)[[:space:]]|--field|--raw-field|--input\b' <<< "$c"; then
        emit_deny "night T2/T3: mutating gh api call. Reads are open; writes queue (T2) or wait for morning (T3). Queue format: night-shift skill."
    fi

    # T3: irreversible / high-risk — no queue, proposals go in the shift report
    if grep -qE '(^|[;&|[:space:](])((npm|pnpm|yarn)[[:space:]]+publish|cargo[[:space:]]+publish|gem[[:space:]]+push|docker[[:space:]]+push|terraform[[:space:]]+(apply|destroy)|kubectl[[:space:]]+(apply|delete|create)|wrangler[[:space:]]+(deploy|publish|versions[[:space:]]+upload)|flyctl[[:space:]]+deploy|vercel([[:space:]]+deploy|.*--prod)|gh[[:space:]]+(release|workflow[[:space:]]+(run|enable|disable)|secret|variable|repo[[:space:]]+(create|delete|edit|fork)))\b' <<< "$c"; then
        emit_deny "night T3: irreversible or high-risk. Not even queued — write it up as a proposal in the shift report; the owner runs it awake."
    fi
    if grep -qE 'path-gate\.sh[[:space:]]+(grant|revoke)|(^|[;&|[:space:](])security[[:space:]]+(add|delete|find)-' <<< "$c"; then
        emit_deny "night T3: the authorization surface (grants, keychain) is frozen while the owner sleeps. The wrappers fetch what they need themselves. Propose anything else in the shift report."
    fi
    if grep -qE '(^|[;&|[:space:](])(claude|claude-night)([[:space:]]|$)' <<< "$c"; then
        emit_deny "night: no nested sessions at night — a child CLI would escape this profile. Use the Agent tool (subagents are covered) or park the task."
    fi

    allow
}

gate_mcp() { # TOOL_NAME
    local t="$1"
    case "$t" in
        mcp__claude_ai_Gmail__send_message | mcp__claude_ai_Gmail__reply | mcp__claude_ai_Gmail__forward)
            emit_deny "night T2: sending email is outbound speech. Put the full text in a queue entry; morning review sends." ;;
        mcp__claude_ai_Google_Calendar__create_event | mcp__claude_ai_Google_Calendar__update_event | mcp__claude_ai_Google_Calendar__delete_event | mcp__claude_ai_Google_Calendar__respond_to_event)
            emit_deny "night T2: calendar mutations can invite or answer other people. Queue it for morning review." ;;
        mcp__claude_ai_Google_Drive__share_file)
            emit_deny "night T2: sharing is outbound. Queue it for morning review." ;;
        mcp__claude_ai_Todoist__*)
            emit_deny "night: Todoist is off the night surface (owner decision) — task management at night is Linear." ;;
        mcp__claude_ai_Interactive_Brokers_IBKR__create_* | mcp__claude_ai_Interactive_Brokers_IBKR__delete_* | mcp__claude_ai_Interactive_Brokers_IBKR__update_* | mcp__claude_ai_Interactive_Brokers_IBKR__edit_* | mcp__claude_ai_Interactive_Brokers_IBKR__set_*)
            emit_deny "night T3: brokerage mutations while the owner sleeps — no. Reads are open; anything else is a shift-report proposal." ;;
        mcp__plugin_playwright_playwright__browser_fill_form | mcp__plugin_playwright_playwright__browser_file_upload | mcp__plugin_playwright_playwright__browser_run_code_unsafe)
            emit_deny "night T2: form submission / upload / arbitrary page JS can post anywhere under logged-in sessions. Browsing stays read-only at night (navigate, snapshot, find)." ;;
    esac
    allow
}

hook_mode() {
    trap hook_exit_backstop EXIT
    # claude-night sessions are capability-narrowed at birth; the blanket
    # stands down there (hooks inherit the session's env).
    [[ "${NIGHT_SESSION:-}" == 1 ]] && allow
    is_active || allow
    local input tool
    input=$(cat)
    tool=$(jq -r '.tool_name // ""' <<< "$input")
    case "$tool" in
        Bash)
            local cmd
            cmd=$(jq -r '.tool_input.command // ""' <<< "$input")
            gate_bash "$cmd"
            ;;
        mcp__*)
            gate_mcp "$tool"
            ;;
    esac
    allow
}

# --- session-start -----------------------------------------------------------

cmd_session_start() {
    if [[ "${1:-}" == "--overlay" ]]; then
        # registered only in the night overlay -> this IS a claude-night session
        jq -n '{
            hookSpecificOutput: {
                hookEventName: "SessionStart",
                additionalContext: "NIGHT SESSION (claude-night): capabilities are narrowed at birth — unsigned commits by construction, git-over-https with tonight'\''s PAT (pushes work only on the repos it was minted for), op answers as the night-vault service account, ssh fails fast, claude.ai connectors are absent. The owner is asleep: load the night-shift skill before any work. A 403/not-found from gh, op, or git means the capability was deliberately not granted tonight: park and record, never work around."
            }
        }'
        exit 0
    fi
    [[ "${NIGHT_SESSION:-}" == 1 ]] && exit 0
    is_active || exit 0
    jq -n '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: "NIGHT MODE is on (machine-wide flag; ends only when the owner runs /night off). This session was NOT started via claude-night, so the night-gate hook constrains it per-call: commits need --no-gpg-sign, network git and op go through ~/.claude/night/bin/git-night and op-night, outbound speech queues, irreversibles are forbidden. Load the night-shift skill before any work. Prefer handing long overnight work to a claude-night session."
        }
    }'
    exit 0
}

# --- CLI ---------------------------------------------------------------------

die() {
    echo "night-gate: $*" >&2
    exit 3
}

cmd_on() {
    (($# == 0)) || die "usage: on (no arguments — night mode is manual and ends only with 'off')"
    jq -n '{since: now}' > "$ACTIVE"
    mkdir -p "$QUEUE_DIR/$(/bin/date +%F)"
    echo "night mode ON — all running Claude Code sessions are now gated; ends only with 'off'"
    echo "queue: $QUEUE_DIR/$(/bin/date +%F)/"
    echo "overnight workhorse: ~/.claude/night/bin/claude-night [--continue]"
}

cmd_off() {
    command rm -f "$ACTIVE"
    echo "night mode OFF"
    local n
    n=$(find "$QUEUE_DIR" -name '*.md' -not -path '*/done/*' -not -path '*/rejected/*' 2> /dev/null | wc -l | tr -d ' ')
    ((n > 0)) && echo "queued entries awaiting review: $n — run /night review"
    exit 0
}

cmd_status() {
    if is_active; then
        local since
        since=$(jq -r '.since | floor' "$ACTIVE" 2> /dev/null || echo "")
        echo "night mode: ON since ${since:+$(/bin/date -r "$since" '+%F %H:%M')}${since:-?} (manual — ends only with 'off')"
    else
        echo "night mode: off"
    fi
    echo "recipe (PAT repos): $(jq -c '.push_allowlist' "$CONFIG" 2> /dev/null || echo '[]')"
    echo "branch convention:  $(cfg '.branch_prefix' 'claude/')* (enforced by repo rulesets + morning review)"
    local n
    for n in "$(cfg '.keychain.sa_token' 'claude-night.op-sa-token')" "$(cfg '.keychain.gh_pat' 'claude-night.gh-pat')"; do
        security find-generic-password -s "$n" > /dev/null 2>&1 && echo "keychain $n: present" || echo "keychain $n: MISSING"
    done
    echo "queue entries (all dates): $(find "$QUEUE_DIR" -name '*.md' 2> /dev/null | wc -l | tr -d ' ')"
}

# Verifies the real channels (network calls, timeout-wrapped), not proxies.
# A dead credential must surface now, not at 2am.
cmd_preflight() {
    local fails=0
    ok() { echo "  ✓ $*"; }
    bad() {
        echo "  ✗ $*"
        fails=$((fails + 1))
    }

    echo "night preflight:"

    if jq -e . "$CONFIG" > /dev/null 2>&1; then
        ok "config parses"
    else
        bad "config.json missing/invalid"
    fi

    local sa_name pat_name vault sa pat
    sa_name=$(cfg '.keychain.sa_token' 'claude-night.op-sa-token')
    pat_name=$(cfg '.keychain.gh_pat' 'claude-night.gh-pat')
    vault=$(cfg '.night_vault' 'night')

    if sa=$(security find-generic-password -s "$sa_name" -w 2> /dev/null); then
        if command -v op > /dev/null 2>&1 \
            && OP_SERVICE_ACCOUNT_TOKEN="$sa" timeout 15 op vault list --format=json 2> /dev/null \
                | jq -e --arg v "$vault" 'map(select(.name == $v)) | length > 0' > /dev/null 2>&1; then
            ok "service account reaches vault '$vault'"
        else
            bad "service account cannot list vault '$vault' (dead token, missing grant, op absent, or offline)"
        fi
    else
        bad "keychain '$sa_name' missing — run the setup wizard"
    fi

    local -a recipe=()
    while IFS= read -r r; do [[ -n "$r" ]] && recipe+=("$r"); done \
        < <(jq -r '.push_allowlist[]?' "$CONFIG" 2> /dev/null)

    if pat=$(security find-generic-password -s "$pat_name" -w 2> /dev/null) \
        && GH_TOKEN="$pat" timeout 15 gh api user -q .login > /dev/null 2>&1; then
        ok "night PAT authenticates"
        local repo short
        for repo in "${recipe[@]}"; do
            short="${repo#github.com/}"
            if GH_TOKEN="$pat" timeout 15 gh api "repos/$short" -q .full_name > /dev/null 2>&1; then
                ok "PAT reaches $short"
            else
                bad "PAT cannot reach $short (not selected at mint time, or repo gone)"
            fi
            if [[ "$(GH_TOKEN="$pat" timeout 15 gh api "repos/$short/rulesets" -q length 2> /dev/null || echo '?')" == "0" ]]; then
                echo "  - $short has no rulesets — force-push/main protection unenforced (wizard stage 4)"
            fi
        done
    elif ((${#recipe[@]} > 0)); then
        echo "  - night PAT missing or dead — claude-night mints tonight's on launch"
    else
        echo "  - no PAT and empty recipe: local-only night (commits stay local)"
    fi

    if "$HOME/.claude/hooks/path-gate.sh" check "$NIGHT_DIR/probe" w > /dev/null 2>&1; then
        ok "path-gate covers $NIGHT_DIR (queue/report writes won't prompt)"
    else
        bad "path-gate does not cover $NIGHT_DIR rw — night sessions would hit unanswerable prompts"
    fi

    if grep -q 'night-gate' "$HOME/.claude/settings.json" 2> /dev/null; then
        ok "hot gate registered in settings.json"
    else
        bad "night-gate hooks not registered in ~/.claude/settings.json — running sessions would be unguarded"
    fi

    if jq -e '.disableClaudeAiConnectors == true and (.permissions.deny | length > 0)' \
        "$NIGHT_DIR/settings.json" > /dev/null 2>&1; then
        ok "night overlay valid (connectors off, guardrails present)"
    else
        bad "night settings overlay missing or malformed: $NIGHT_DIR/settings.json"
    fi

    local b
    for b in claude-night git-night op-night; do
        if [[ -x "$NIGHT_DIR/bin/$b" ]]; then ok "$b executable"; else bad "$b missing or not executable"; fi
    done

    echo
    if ((fails > 0)); then
        echo "preflight FAILED ($fails) — do not start the shift."
        exit 1
    fi
    echo "preflight clean."
}

# --- dispatch ----------------------------------------------------------------

if (($# == 0)); then
    hook_mode
fi

cmd="$1"
shift
case "$cmd" in
    on) cmd_on "$@" ;;
    off) cmd_off ;;
    status) cmd_status ;;
    preflight) cmd_preflight ;;
    session-start) cmd_session_start "$@" ;;
    *) die "unknown command '$cmd' (on | off | status | preflight | session-start, or no args for hook mode)" ;;
esac
