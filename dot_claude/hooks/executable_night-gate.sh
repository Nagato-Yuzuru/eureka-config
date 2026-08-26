#!/usr/bin/env bash
# night-gate v5: night-mode operations + the tool-name gate.
#
# Night mode's entire state is one byte: ~/.claude/night/active.json exists
# or it doesn't. Enforcement lives ONLY where mediation is complete:
#
#   ISSUANCE (GitHub server side; fnox night profile). The night App's
#     permissions and installation list ARE the authority boundary: 8h
#     device-flow lease, Contents RW only, no PR/issue/workflow perms,
#     keyring_cache=false discards the refresh token. What is not issued
#     cannot be done; a 403 is the tier working.
#   SHIM ARGV (~/.claude/night/bin/night). The night system's only command
#     inspection, on real argv at the credential injection point: the
#     within-Contents:write delta (force-push, ref deletes, default-branch
#     pushes, release writes, mutating gh api).
#   HYGIENE (zshenv -> night/env.sh; ssh-night.conf). Subtractive env, hot
#     per command, transitive: signing off, gh isolated, ssh agent gone, op
#     sentinel. Credential-shaped bare commands die in milliseconds with
#     the real error. This is the FEEDBACK layer; the night-shift skill
#     teaches how to read it (a denial is a capability not granted tonight:
#     park it).
#   TOOL NAMES (this hook). The only ambient authority no lease can starve:
#     browser sessions and claude.ai connectors. Tool names cannot be
#     respelled, so allowlists here are a real boundary.
#
# Bash strings are NEVER parsed: a hook sees the actor's own description of
# an action, not the action, so command rules here would be either theater
# or false-deny generators. The threat model is drift (a confused-but-
# compliant agent), not an adversary; honesty means real errors from real
# boundaries, not simulated denials from a parser.
#
# Modes:
#   (no args)      PreToolUse gate: inert unless the flag exists
#   user-prompt    UserPromptSubmit: once per session, tell a live session
#                  the flag is on and to load the night-shift skill
#   session-start  SessionStart context while the flag is on
#   on [--mode full|local-only] [--scope csv]   flip the flag
#   off            end the night
#   status         flag / mode / scope / queue at a glance
#   preflight      three-verdict: exit 0 PASS / 2 DEGRADE (local-only) / 1 FAIL

set -euo pipefail

NIGHT_DIR="${NIGHT_DIR:-$HOME/.claude/night}"
ACTIVE="$NIGHT_DIR/active.json"
QUEUE_DIR="$NIGHT_DIR/queue"
SESSIONS_DIR="$NIGHT_DIR/sessions"
NIGHT_SHIM="$NIGHT_DIR/bin/night"

# presence-only flag: manual on/off, no time semantics. A corrupt flag file
# is still a file: fail-closed.
is_active() { [[ -e "$ACTIVE" ]]; }

# --- hook mode (PreToolUse) --------------------------------------------------

# Fail-closed backstop: a crash mid-decision must deny, not ask. At night
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

gate_mcp() { # TOOL_NAME
    local t="$1"
    case "$t" in
        mcp__linear-bot__*)
            # Linear is the night task tracker: allow-listed to reads plus
            # private task state, not open. save_issue/save_comment are T0
            # (Linear-internal notes, the skill's own queue/report mirror),
            # NOT the tier table's T2 "comment", which means public GitHub
            # PR/issue speech. Merging diffs, submitting reviews and
            # publishing issues act on the world: deny. Task-state writes
            # ride THIS connection (the "Night Shift Agent" app identity),
            # so the tracker records the agent, not the owner.
            case "${t#mcp__linear-bot__}" in
                list_* | get_* | search_documentation | extract_images | save_issue | save_comment) allow ;;
                *)
                    emit_deny "night: Linear is allow-listed to reads + task state (list_*/get_*/save_issue/save_comment). '$t' merges, reviews or publishes — T2/T3 outbound: queue it or propose it in the shift report." ;;
            esac
            ;;
        mcp__linear-server__*)
            # The owner-identity connection is read-only at night: writes
            # through it would attribute agent activity to the owner.
            case "${t#mcp__linear-server__}" in
                list_* | get_* | search_documentation | extract_images) allow ;;
                save_issue | save_comment)
                    emit_deny "night: task-state writes carry the agent identity — use the mcp__linear-bot__ equivalent of '$t' so the tracker records the agent, not the owner." ;;
                *)
                    emit_deny "night: Linear is allow-listed to reads + task state (list_*/get_* here; save_issue/save_comment via linear-bot). '$t' merges, reviews or publishes — T2/T3 outbound: queue it or propose it in the shift report." ;;
            esac
            ;;
        mcp__claude_ai_*)
            emit_deny "night: claude.ai connectors are off the night surface entirely — reads included (unattended reading of mail/files is a prompt-injection channel). Night task tracking is Linear." ;;
        mcp__claude-in-chrome__*)
            # Browser sessions are ambient authority no lease can starve,
            # so the browser gets a read ALLOWLIST: a tool this list has
            # never seen (new, or write-class) is denied by default.
            case "${t#mcp__claude-in-chrome__}" in
                navigate | go_back | go_forward | read_page | get_page_text | find | screenshot | tabs_context_mcp | read_console_messages) allow ;;
                *)
                    emit_deny "night: browsing is read-only (browser sessions are logged-in ambient authority). '$t' is not on the read allowlist — write-class or unknown: park it and record it." ;;
            esac
            ;;
        mcp__plugin_playwright_playwright__*)
            case "${t#mcp__plugin_playwright_playwright__}" in
                browser_snapshot | browser_navigate | browser_navigate_back | browser_take_screenshot | browser_console_messages | browser_network_requests | browser_find | browser_wait_for | browser_tabs | browser_resize | browser_close) allow ;;
                *)
                    emit_deny "night: browsing is read-only. '$t' is not on the read allowlist — write-class or unknown: park it and record it." ;;
            esac
            ;;
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
    local input='' tool
    IFS= read -r -d '' input || : # read to EOF; no external cat
    tool=$(jq -r '.tool_name // ""' <<< "$input")
    case "$tool" in
        mcp__*) gate_mcp "$tool" ;;
    esac
    # Bash included: zero string parsing, by design (see header).
    allow
}

# --- user-prompt (UserPromptSubmit) ------------------------------------------

# A session alive when the flag falls has no SessionStart moment; its first
# prompt afterwards gets one injection pointing at the night-shift skill.
# Marker per session_id: exactly once, per session, per night.
cmd_user_prompt() {
    is_active || exit 0
    local input='' sid
    IFS= read -r -d '' input || :
    sid=$(jq -r '.session_id // ""' <<< "$input" | tr -cd 'A-Za-z0-9_-')
    [[ -n "$sid" ]] || exit 0
    [[ -e "$SESSIONS_DIR/$sid" ]] && exit 0
    mkdir -p "$SESSIONS_DIR"
    : > "$SESSIONS_DIR/$sid"
    jq -n '{
        hookSpecificOutput: {
            hookEventName: "UserPromptSubmit",
            additionalContext: "NIGHT MODE is on (machine-wide flag; ends only when the owner runs /night off). The owner is asleep. Before acting on this prompt, load the night-shift skill and follow it: hygiene is already ambient in every shell, credentialed commands go through ~/.claude/night/bin/night <cmd>, outbound speech is queued, and a 403 or fast credential error is a capability deliberately not granted tonight — park it and record it, never work around it."
        }
    }'
    exit 0
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
            additionalContext: ("NIGHT MODE is on (machine-wide flag; ends only when the owner runs /night off). The owner is asleep: load the night-shift skill before any work. Hygiene is automatic in every shell (signing off, ssh dead, gh isolated, op sentinel); credentialed commands go through ~/.claude/night/bin/night <cmd>. A 403, deny, or fast credential error is a capability the owner deliberately did not grant tonight: park it and record it, never work around it." + $extra)
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
                [[ -n "${2-}" ]] || die "--mode needs a value (full|local-only)"
                case "$2" in
                    full | local-only) mode="$2" ;;
                    *) die "unknown --mode '$2' (full|local-only)" ;;
                esac
                shift 2
                ;;
            --scope)
                [[ -n "${2-}" ]] || die "--scope needs a comma-separated repo list"
                scope=$(printf '%s' "$2" | jq -R 'split(",") | map(select(length > 0))')
                shift 2
                ;;
            *) die "usage: on [--mode full|local-only] [--scope repo1,repo2]" ;;
        esac
    done
    local today ts
    today=$(date -u +%F)
    ts=$(date -u +%FT%TZ) # ISO in the flag: portable to read back anywhere
    mkdir -p "$NIGHT_DIR" "$QUEUE_DIR/$today" "$NIGHT_DIR/reports" "$SESSIONS_DIR"
    # day daemon caches must not cross the boundary; idempotent, best-effort
    # (fnox absent or no daemon running is not an arming failure)
    command -v fnox > /dev/null 2>&1 && fnox daemon clear > /dev/null 2>&1 || true
    jq -n --arg m "$mode" --argjson s "$scope" --arg ts "$ts" \
        '{since: $ts, mode: $m, scope: $s, confirmed_at: $ts}' > "$ACTIVE"
    echo "night mode ON ($mode) — every Claude session on this machine is now governed; ends only with 'off'"
    echo "queue: $QUEUE_DIR/$today/"
}

cmd_off() {
    command rm -f "$ACTIVE"
    command rm -rf "$SESSIONS_DIR" # next night re-notifies every session
    echo "night mode OFF"
    local n
    n=$(find "$QUEUE_DIR" -name '*.md' -not -path '*/done/*' -not -path '*/rejected/*' 2> /dev/null | wc -l | tr -d ' ' || true)
    ((n > 0)) && echo "queued entries awaiting review: $n — run /night review"
    exit 0
}

cmd_status() {
    if is_active; then
        local since mode
        since=$(jq -r '.since // empty' "$ACTIVE" 2> /dev/null || true)
        mode=$(jq -r '.mode // "full"' "$ACTIVE" 2> /dev/null || echo full)
        if [[ -n "$since" ]]; then
            echo "night mode: ON ($mode) since $since"
        else
            echo "night mode: ON ($mode)"
        fi
        echo "scope (arm-time snapshot, record not truth): $(jq -c '.scope // []' "$ACTIVE" 2> /dev/null || echo '[]')"
        if command -v fnox > /dev/null 2>&1; then
            echo "lease: $(fnox -c "$HOME/.config/fnox/config.toml" -P night lease list --active 2> /dev/null | tail -n +2 | head -3 || echo '?')"
        fi
    else
        echo "night mode: off"
    fi
    echo "queue entries (all dates): $(find "$QUEUE_DIR" -name '*.md' 2> /dev/null | wc -l | tr -d ' ')"
}

# Three-verdict preflight. FAIL = the fabric is broken (hook, hygiene,
# keychain, shim): do not start a shift. DEGRADE = only the GitHub lease is
# unavailable: a local-only night is possible. Verifies real channels and
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

    # the hook: registered for the two events that matter, executable, and
    # both decision paths fire on the REGISTERED hook command (not this
    # file: a stale deployment must show up here)
    local entry hookcmd='' matcher=''
    entry=$(jq -c '[.hooks.PreToolUse[]? | select([.hooks[]?.command // empty] | any(test("night-gate")))] | .[0] // empty' "$HOME/.claude/settings.json" 2> /dev/null || true)
    if [[ -n "$entry" ]]; then
        hookcmd=$(jq -r '[.hooks[].command] | .[0]' <<< "$entry")
        matcher=$(jq -r '.matcher // ""' <<< "$entry")
        if [[ -x "${hookcmd%% *}" ]]; then
            ok "gate registered and executable"
        else
            bad "night-gate registered but not executable ($hookcmd)"
        fi
        if [[ "mcp__linear-server__get_issue" =~ $matcher ]]; then
            ok "PreToolUse matcher covers mcp__ tools"
        else
            bad "matcher '$matcher' does not cover mcp__ tools"
        fi
    else
        bad "night-gate not registered for PreToolUse in ~/.claude/settings.json"
    fi
    if jq -e '[.hooks.UserPromptSubmit[]? | select([.hooks[]?.command // empty] | any(test("night-gate")))] | length > 0' "$HOME/.claude/settings.json" > /dev/null 2>&1; then
        ok "user-prompt notifier registered (live sessions learn the flag fell)"
    else
        bad "night-gate user-prompt not registered for UserPromptSubmit"
    fi
    if [[ -n "$hookcmd" ]]; then
        local fire
        fire=$(printf '%s' '{"tool_name":"mcp__claude_ai_Gmail__get_message","tool_input":{}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if jq -e '(.hookSpecificOutput.permissionDecision == "deny") and (.hookSpecificOutput.permissionDecisionReason | test("connector"))' <<< "$fire" > /dev/null 2>&1; then
            ok "gate live-fire denies a connector tool (registered hook, forced on)"
        else
            bad "gate live-fire deny failed — the registered hook's deny path is broken"
        fi
        fire=$(printf '%s' '{"tool_name":"mcp__linear-server__list_issues","tool_input":{}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if [[ -z "$fire" ]]; then
            ok "gate live-fire allows Linear reads (silent)"
        else
            bad "gate live-fire allow path broken — allowed tool produced output"
        fi
    fi

    # hygiene channel: the deployed env.sh, really sourced in a clean shell,
    # must set the real values, not just exist under the right filename
    if [[ -f "$HOME/.claude/night/env.sh" ]]; then ok "env.sh present"; else bad "env.sh missing"; fi
    if grep -q 'night/env.sh' "$HOME/.zshenv" 2> /dev/null; then ok "zshenv guard present"; else bad "zshenv guard missing from ~/.zshenv"; fi
    local hy
    # shellcheck disable=SC2016  # the zsh script must expand in zsh, not here
    hy=$(env -i HOME="$HOME" PATH="$PATH" CLAUDECODE=1 zsh -c \
        'source "$HOME/.claude/night/env.sh"; print -r -- "$GH_CONFIG_DIR|$FNOX_NON_INTERACTIVE|$SSH_AUTH_SOCK|${OP_SERVICE_ACCOUNT_TOKEN%%_*}|$(git config --get commit.gpgsign)|$(git config --get tag.gpgsign)|$(git config --get core.sshCommand)"' 2> /dev/null || true)
    if [[ "$hy" == "$HOME/.claude/night/gh-null|1||ops|false|false|/usr/bin/false" ]]; then
        ok "hygiene env sources clean: gh isolated, fnox headless, ssh agent dead, op sentinel armed, signing off, git-over-ssh killed"
    else
        bad "hygiene env content wrong when sourced (got '$hy')"
    fi
    # feedback layer, really exercised: credential-shaped bare commands must
    # die fast (not hang on anything lockable) with a readable error
    if command -v gh > /dev/null 2>&1; then
        if timeout 5 env GH_CONFIG_DIR="$HOME/.claude/night/gh-null" GH_TOKEN= GITHUB_TOKEN= gh api user > /dev/null 2>&1; then
            bad "bare gh with the night config dir unexpectedly SUCCEEDED (day auth leaking in)"
        else
            ok "bare gh dies fast under night gh isolation"
        fi
    fi
    if command -v op > /dev/null 2>&1; then
        if timeout 5 env OP_SERVICE_ACCOUNT_TOKEN="ops_NIGHT_SENTINEL_invalid_on_purpose_use_night_op" op vault list > /dev/null 2>&1; then
            bad "op with the sentinel token unexpectedly SUCCEEDED"
        else
            ok "op dies fast on the sentinel token (no desktop-app hang path)"
        fi
    fi
    # ssh: the Match-exec include must be wired at the TOP of ~/.ssh/config
    if [[ -f "$HOME/.claude/night/ssh-night.conf" ]]; then ok "ssh-night.conf present"; else bad "ssh-night.conf missing"; fi
    if head -5 "$HOME/.ssh/config" 2> /dev/null | grep -q 'night/ssh-night.conf'; then
        ok "ssh config Includes ssh-night.conf near the top"
    else
        bad "ssh config at \$HOME/.ssh/config does not Include night/ssh-night.conf in its first lines (ssh keeps the FIRST value — the Include must come before any IdentityAgent)"
    fi
    # regression: on-disk private keys would let night ssh succeed as the owner
    if grep -lE -- '-----BEGIN .*PRIVATE KEY' "$HOME"/.ssh/* 2> /dev/null | grep -q .; then
        bad "private key files on disk in ~/.ssh — night ssh could succeed with the owner's identity"
    else
        ok "no on-disk ssh private keys"
    fi
    # file-tool writes to the queue/report dirs must not prompt at night
    if "$HOME/.claude/hooks/path-gate.sh" check "$NIGHT_DIR" w > /dev/null 2>&1; then
        ok "path-gate grants w on $NIGHT_DIR (queue + report writes)"
    else
        bad "path-gate denies w on $NIGHT_DIR — queue/report writes would prompt with nobody awake"
    fi

    # credential channel: real resolutions, not existence checks
    if [[ -x "$NIGHT_SHIM" ]]; then ok "night shim executable"; else bad "night shim missing"; fi
    if command -v fnox > /dev/null 2>&1; then
        ok "fnox present ($(fnox --version 2> /dev/null || echo '?'))"
    else
        bad "fnox not installed"
    fi
    # linear-bot: night task-state writes carry the agent identity; a dead
    # token turns them into fast failures mid-shift
    if [[ -f "$NIGHT_DIR/linear-bot-expiry" ]]; then
        local exp days
        exp=$(cat "$NIGHT_DIR/linear-bot-expiry" 2> /dev/null || echo 0)
        days=$(((exp - $(date +%s)) / 86400))
        if ((days < 0)); then
            soft "linear-bot token expired — run linear-bot-remint (day, presence-gated)"
        elif ((days < 5)); then
            soft "linear-bot token expires in ${days}d — run linear-bot-remint before a long stretch"
        else
            ok "linear-bot token alive (${days}d left)"
        fi
    else
        soft "linear-bot token expiry unknown — run linear-bot-remint once"
    fi
    # lease-independent on purpose: `fnox get` resolves the single secret, so
    # a dead GitHub lease cannot mask a healthy (or broken) 1Password channel
    local sa
    if ! command -v op > /dev/null 2>&1; then
        soft "op absent — night vault channel unavailable on this machine (linux custody: YUZ-80)"
    elif sa=$(FNOX_NON_INTERACTIVE=1 fnox -c "$HOME/.config/fnox/config.toml" -P night get OP_SERVICE_ACCOUNT_TOKEN 2> /dev/null) \
        && OP_SERVICE_ACCOUNT_TOKEN="$sa" timeout 20 op vault list --format=json 2> /dev/null \
        | jq -e 'map(.name) == ["Night"]' > /dev/null 2>&1; then
        ok "SA reaches exactly the night vault (keychain ACL + 1Password, end to end)"
    else
        bad "SA cannot list exactly the night vault (keychain ACL? dead token?)"
    fi

    # the shim itself must exec before the lease question is even asked:
    # a broken shim is FAIL (fabric), never mistaken for a missing lease
    local shim_ok=0
    if FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" true > /dev/null 2>&1; then
        ok "night shim execs end to end (fnox exec path)"
        shim_ok=1
    else
        bad "night shim cannot exec a trivial command — shim/fnox wiring broken (not a lease question)"
    fi

    # lease: absent => DEGRADE (local-only), not FAIL
    local login
    if ((shim_ok)) && login=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user -q .login 2> /dev/null); then
        ok "github lease alive (as $login)"
        echo "  scope — installation repositories (the enforced allowlist):"
        FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user/installations --jq '.installations[].id' 2> /dev/null \
            | while read -r iid; do
                FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api "user/installations/$iid/repositories" --jq '.repositories[].full_name' 2> /dev/null | sed 's/^/    - /'
            done || true
    elif ((shim_ok)); then
        soft "github lease absent/expired — tonight would be LOCAL-ONLY (mint interactively: $NIGHT_SHIM gh api user)"
    fi

    echo
    if ((fails > 0)); then
        echo "preflight FAILED ($fails) — do not start the shift."
        exit 1
    fi
    if ((degraded)); then
        echo "preflight DEGRADED — a reduced night is available (see ~ lines above)."
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
    user-prompt) cmd_user_prompt ;;
    *) die "unknown command '$cmd' (on | off | status | preflight | session-start | user-prompt, or no args for hook mode)" ;;
esac
