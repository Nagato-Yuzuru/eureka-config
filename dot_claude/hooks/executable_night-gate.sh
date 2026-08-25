#!/usr/bin/env bash
# night-gate v4 — authority gate + night-mode operations for Claude Code.
#
# Night mode's entire state is one byte: ~/.claude/night/active.json exists
# or it doesn't. Three channels enforce it, all driven by that flag:
#
#   hook (this file; permanently registered, inert without the flag) — the
#     AUTHORITY layer: outbound speech, irreversibles, connectors, browser
#     writes, and the authorization surface itself;
#   ~/.zshenv sourcing night/env.sh — the HYGIENE layer (signing off, gh
#     isolation, empty ssh agent, headless fnox), hot for every Claude shell
#     and transitive to every descendant process;
#   fnox night profile via bin/night — the CREDENTIAL layer: what is declared
#     there is the complete night capability surface.
#
# The capability layer carries what it can: the night App's permissions 403
# PR/issue creation, its installation scopes pushes, the missing Workflows
# permission freezes CI files. This hook covers only what capabilities cannot
# express. Regex here is drift protection, not an adversary boundary.
#
# Modes:
#   (no args)      PreToolUse gate: inert unless the flag exists
#   on [--mode full|local-only] [--scope csv]
#                  flip the flag (owner's call; /night on orchestrates
#                  preflight + lease mint + scope confirm first)
#   off            end the night
#   status         flag / mode / scope / queue at a glance
#   preflight      three-verdict: exit 0 PASS / 2 DEGRADE (local-only) / 1 FAIL
#   session-start  SessionStart context while the flag is on

set -euo pipefail

NIGHT_DIR="${NIGHT_DIR:-$HOME/.claude/night}"
ACTIVE="$NIGHT_DIR/active.json"
QUEUE_DIR="$NIGHT_DIR/queue"
NIGHT_SHIM="$NIGHT_DIR/bin/night"

# presence-only flag: manual on/off, no time semantics. A corrupt flag file
# is still a file: fail-closed.
is_active() { [[ -e "$ACTIVE" ]]; }

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

W='(^|[;&|[:space:](])' # word-ish start for command tokens

gate_bash() { # CMD
    local c="$1"

    # The sanctioned credential path. Commands routed through the shim skip
    # the bare-tool reroutes below (the shim execs real binaries with the
    # night lease); shim-shaped T3 is still checked first.
    local via_shim=0
    grep -qE "${W}([^[:space:]]*/)?night[[:space:]]" <<< "$c" && via_shim=1

    # T3 through the shim: releases ride on Contents:write — the one hole
    # the capability layer cannot close, so this line is load-bearing.
    if grep -qE "night[[:space:]]+gh[[:space:]]+release([[:space:]]|$)" <<< "$c" \
        || { grep -qE "night[[:space:]]+gh[[:space:]]+api" <<< "$c" \
            && grep -qiE -- '-X[[:space:]]*(POST|PUT|PATCH|DELETE)|--method[[:space:]=]*(POST|PUT|PATCH|DELETE)|(^|[[:space:]])-(f|F)[[:space:]]|--field|--raw-field|--input' <<< "$c"; }; then
        emit_deny "night T3: mutating GitHub state beyond pushes (releases live under the Contents permission). T2-shaped speech goes in the queue; T3 is a shift-report proposal."
    fi

    # authorization surface frozen while the owner sleeps
    if grep -qE "${W}fnox([[:space:]]|$)" <<< "$c" || grep -q 'FNOX_PROFILE=' <<< "$c"; then
        emit_deny "night: direct fnox use is frozen — credentialed commands go through ~/.claude/night/bin/night <cmd>; secrets and grants change only while the owner is awake."
    fi
    if grep -qE "${W}security[[:space:]]+(add|delete|find)-" <<< "$c" \
        || grep -qE 'path-gate\.sh[[:space:]]+(grant|revoke)' <<< "$c"; then
        emit_deny "night T3: the authorization surface (keychain, path grants) is frozen while the owner sleeps. Propose changes in the shift report."
    fi

    # ssh-shaped: ~/.ssh/config pins IdentityAgent at the 1Password socket —
    # the environment cannot override ssh_config, and the locked agent hangs.
    if grep -qE "${W}(ssh|scp|sftp|rsync)([[:space:]]|$)" <<< "$c"; then
        emit_deny "night: ssh goes through the locked 1Password agent (ssh_config IdentityAgent — env cannot override it) and would hang until morning. git already talks https via the hygiene env; anything else ssh-shaped: park it and record it in the shift report."
    fi

    if [[ $via_shim == 0 ]]; then
        # gh/hcloud are 1Password shell-plugin functions in Claude shells:
        # they hit the locked desktop app. The shim execs the real binaries.
        if grep -qE "${W}(gh|hcloud)([[:space:]]|$)" <<< "$c"; then
            emit_deny "night: bare gh/hcloud run through the locked 1Password plugin and hang. Use ~/.claude/night/bin/night <same command> — real binary, tonight's lease. A 403 there is a capability the owner deliberately did not grant tonight: park it and record it."
        fi
        # interactive op (the night SA is injected only inside the shim)
        if grep -qE "${W}op([[:space:]]|$)" <<< "$c"; then
            emit_deny "night: interactive op hits the locked desktop app and hangs. Use ~/.claude/night/bin/night op <args> — answers as the night-vault service account; an item outside that vault is unavailable tonight: park it."
        fi
    fi

    # T3 irreversibles / high-risk — depth behind the capability layer
    if grep -qE "${W}((npm|pnpm|yarn)[[:space:]]+publish|cargo[[:space:]]+publish|gem[[:space:]]+push|docker[[:space:]]+push|terraform[[:space:]]+(apply|destroy)|kubectl[[:space:]]+(apply|delete|create)|wrangler[[:space:]]+(deploy|publish)|flyctl[[:space:]]+deploy|vercel([[:space:]]+deploy|.*--prod))" <<< "$c"; then
        emit_deny "night T3: irreversible or high-risk. Not even queued — write it up as a proposal in the shift report; the owner runs it awake."
    fi

    # no nested CLI sessions: an uncontrolled loop in a non-TTY; subagents
    # are already covered by this same gate
    if grep -qE "${W}(claude|claude-night)([[:space:]]|$)" <<< "$c"; then
        emit_deny "night: no nested CLI sessions. Use the Agent tool (subagents inherit this gate) or park the task."
    fi

    allow
}

gate_mcp() { # TOOL_NAME
    local t="$1"
    case "$t" in
        mcp__claude_ai_*)
            emit_deny "night: claude.ai connectors are off the night surface entirely — reads included (unattended reading of mail/files is a prompt-injection channel). Night task tracking is Linear." ;;
        mcp__claude-in-chrome__computer | mcp__claude-in-chrome__javascript_tool | mcp__claude-in-chrome__form_input | mcp__claude-in-chrome__file_upload | mcp__claude-in-chrome__upload_image | mcp__claude-in-chrome__browser_batch | mcp__claude-in-chrome__shortcuts_execute)
            emit_deny "night T2: browser write/execute under logged-in sessions can post anywhere. Browsing is read-only at night (navigate, read_page, get_page_text, find, screenshots)." ;;
        mcp__plugin_playwright_playwright__browser_click | mcp__plugin_playwright_playwright__browser_type | mcp__plugin_playwright_playwright__browser_fill_form | mcp__plugin_playwright_playwright__browser_file_upload | mcp__plugin_playwright_playwright__browser_run_code_unsafe | mcp__plugin_playwright_playwright__browser_evaluate | mcp__plugin_playwright_playwright__browser_press_key | mcp__plugin_playwright_playwright__browser_drag | mcp__plugin_playwright_playwright__browser_drop | mcp__plugin_playwright_playwright__browser_select_option | mcp__plugin_playwright_playwright__browser_handle_dialog | mcp__plugin_playwright_playwright__browser_network_request)
            emit_deny "night T2: browser write/execute. Browsing is read-only at night." ;;
    esac
    allow
}

hook_mode() {
    trap hook_exit_backstop EXIT
    # NIGHT_GATE_FORCE can only ACTIVATE the gate (preflight live-fire),
    # never bypass it.
    if ! is_active && [[ "${NIGHT_GATE_FORCE:-}" != 1 ]]; then
        allow
    fi
    local input tool
    input=$(cat)
    tool=$(jq -r '.tool_name // ""' <<< "$input")
    case "$tool" in
        Bash)
            gate_bash "$(jq -r '.tool_input.command // ""' <<< "$input")"
            ;;
        mcp__*)
            gate_mcp "$tool"
            ;;
    esac
    allow
}

# --- session-start -----------------------------------------------------------

cmd_session_start() {
    is_active || exit 0
    local mode extra=""
    mode=$(jq -r '.mode // "full"' "$ACTIVE" 2> /dev/null || echo full)
    [[ "$mode" == "local-only" ]] && extra=" TONIGHT IS LOCAL-ONLY: the GitHub lease is absent, and fnox deliberately aborts on a dead lease — night gh, night git pushes AND night op are all unavailable. Commits stay local; park anything needing credentials."
    jq -n --arg extra "$extra" '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: ("NIGHT MODE is on (machine-wide flag; ends only when the owner runs /night off). The owner is asleep: load the night-shift skill before any work. Hygiene is automatic in every shell (signing off, ssh dead, gh isolated); credentialed commands go through ~/.claude/night/bin/night <cmd>. A 403 or deny is a capability the owner deliberately did not grant tonight: park it and record it, never work around it." + $extra)
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
    local mode="full" scope="[]"
    while (($#)); do
        case "$1" in
            --mode)
                mode="$2"
                shift 2
                ;;
            --scope)
                scope=$(printf '%s' "$2" | jq -R 'split(",") | map(select(length > 0))')
                shift 2
                ;;
            *) die "usage: on [--mode full|local-only] [--scope repo1,repo2]" ;;
        esac
    done
    jq -n --arg m "$mode" --argjson s "$scope" \
        '{since: (now | floor), mode: $m, scope: $s, confirmed_at: (now | floor)}' > "$ACTIVE"
    mkdir -p "$QUEUE_DIR/$(/bin/date +%F)"
    echo "night mode ON ($mode) — every Claude session on this machine is now governed; ends only with 'off'"
    echo "queue: $QUEUE_DIR/$(/bin/date +%F)/"
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
        local since mode
        since=$(jq -r '.since // empty' "$ACTIVE" 2> /dev/null || true)
        mode=$(jq -r '.mode // "full"' "$ACTIVE" 2> /dev/null || echo full)
        if [[ -n "$since" ]]; then
            echo "night mode: ON ($mode) since $(/bin/date -r "$since" '+%F %H:%M')"
        else
            echo "night mode: ON ($mode)"
        fi
        echo "scope (arm-time snapshot, record not truth): $(jq -c '.scope // []' "$ACTIVE" 2> /dev/null || echo '[]')"
    else
        echo "night mode: off"
    fi
    echo "queue entries (all dates): $(find "$QUEUE_DIR" -name '*.md' 2> /dev/null | wc -l | tr -d ' ')"
}

# Three-verdict preflight. FAIL = the fabric is broken (gate, hygiene,
# keychain, shim) — do not start a shift. DEGRADE = only the GitHub lease is
# unavailable — a local-only night is possible. Verifies real channels and
# the enforcement machinery itself, never proxies.
cmd_preflight() {
    local fails=0 degraded=0
    ok() { echo "  ✓ $*"; }
    bad() {
        echo "  ✗ $*"
        fails=$((fails + 1))
    }
    soft() {
        echo "  ~ $*"
        degraded=1
    }

    echo "night preflight:"

    # the gate itself: registered, executable, and its deny path actually fires
    local hookcmd
    hookcmd=$(jq -r '[.hooks.PreToolUse[]?.hooks[]?.command // empty] | map(select(test("night-gate"))) | .[0] // empty' "$HOME/.claude/settings.json" 2> /dev/null || true)
    if [[ -n "$hookcmd" && -x "${hookcmd%% *}" ]]; then
        ok "gate registered and executable"
    else
        bad "night-gate not registered/executable in ~/.claude/settings.json"
    fi
    if printf '{"tool_name":"Bash","tool_input":{"command":"fnox get X"}}' \
        | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash "${BASH_SOURCE[0]}" 2> /dev/null \
        | jq -e '.hookSpecificOutput.permissionDecision == "deny"' > /dev/null 2>&1; then
        ok "gate live-fire denies (synthetic forced-on call)"
    else
        bad "gate live-fire failed — the deny path is broken"
    fi

    # hygiene channel
    if [[ -f "$NIGHT_DIR/env.sh" ]]; then ok "env.sh present"; else bad "env.sh missing"; fi
    if grep -q 'night/env.sh' "$HOME/.zshenv" 2> /dev/null; then ok "zshenv guard present"; else bad "zshenv guard missing from ~/.zshenv"; fi
    # regression: on-disk private keys would let night ssh succeed as the owner
    if grep -lE -- '-----BEGIN .*PRIVATE KEY' "$HOME"/.ssh/* 2> /dev/null | grep -q .; then
        bad "private key files on disk in ~/.ssh — night ssh could succeed with the owner's identity"
    else
        ok "no on-disk ssh private keys"
    fi

    # credential channel: real resolutions, not existence checks
    if [[ -x "$NIGHT_SHIM" ]]; then ok "night shim executable"; else bad "night shim missing"; fi
    if command -v fnox > /dev/null 2>&1; then
        ok "fnox present ($(fnox --version 2> /dev/null || echo '?'))"
    else
        bad "fnox not installed"
    fi
    # lease-independent on purpose: `fnox get` resolves the single secret, so
    # a dead GitHub lease cannot mask a healthy (or broken) 1Password channel
    local sa
    if sa=$(FNOX_NON_INTERACTIVE=1 fnox -c "$HOME/.config/fnox/config.toml" -P night get OP_SERVICE_ACCOUNT_TOKEN 2> /dev/null) \
        && OP_SERVICE_ACCOUNT_TOKEN="$sa" timeout 20 op vault list --format=json 2> /dev/null \
        | jq -e 'map(.name) == ["night"]' > /dev/null 2>&1; then
        ok "SA reaches exactly the night vault (keychain ACL + 1Password, end to end)"
    else
        bad "SA cannot list exactly the night vault (keychain ACL? dead token? op missing?)"
    fi

    # lease: absent => DEGRADE (local-only), not FAIL
    local login
    if login=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user -q .login 2> /dev/null); then
        ok "github lease alive (as $login)"
        echo "  scope — installation repositories (the enforced allowlist):"
        FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user/installations --jq '.installations[].id' 2> /dev/null \
            | while read -r iid; do
                FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api "user/installations/$iid/repositories" --jq '.repositories[].full_name' 2> /dev/null | sed 's/^/    - /'
            done
    else
        soft "github lease absent/expired — tonight would be LOCAL-ONLY (mint interactively: $NIGHT_SHIM gh api user)"
    fi

    echo
    if ((fails > 0)); then
        echo "preflight FAILED ($fails) — do not start the shift."
        exit 1
    fi
    if ((degraded)); then
        echo "preflight DEGRADED — local-only night available (pushes and gh unavailable)."
        exit 2
    fi
    echo "preflight PASS."
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
    session-start) cmd_session_start ;;
    *) die "unknown command '$cmd' (on | off | status | preflight | session-start, or no args for hook mode)" ;;
esac
