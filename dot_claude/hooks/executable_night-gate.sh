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
#   SHIM (~/.claude/night/bin/night). The night system's only command
#     inspection, at the credential injection point — the within-
#     Contents:write delta. git push is judged on ref-update objects from
#     `git push --dry-run --porcelain` (rewrites, deletes, the resolved
#     default branch — YUZ-87); gh on real argv (release writes, mutating
#     gh api).
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
                    emit_deny "night: task-state writes carry the agent identity — use the mcp__linear-bot__ equivalent of '$t' so the tracker records the agent, not the owner. If the linear-bot connection is absent tonight (preflight said so at arm time), record the update in the shift report instead." ;;
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

# The hygiene fingerprint: ONE payload, two loaders. Preflight sources
# env.sh directly (content check, works while the flag is down); cmd_on
# re-runs it POST-FLIP through the real zshenv chain (wiring check, only
# possible once the flag exists). Values must match HY_WANT exactly.
# shellcheck disable=SC2016  # the probe must expand in zsh, not here
HY_PROBE='print -r -- "$GH_CONFIG_DIR|$FNOX_NON_INTERACTIVE|$SSH_AUTH_SOCK|${OP_SERVICE_ACCOUNT_TOKEN%%_*}|$(git config --get commit.gpgsign)|$(git config --get tag.gpgsign)|$(git config --get core.sshCommand)"'
HY_WANT="$HOME/.claude/night/gh-null|1||ops|false|false|/usr/bin/false"

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
    # Post-flip re-verification (YUZ-88): zshenv wiring and the ssh Match
    # are only testable END TO END while the flag exists. Verify now and
    # revert rather than leave a night armed on dead hygiene.
    local pf_fail='' hy sshg
    hy=$(env -i HOME="$HOME" PATH="$PATH" CLAUDECODE=1 zsh -c "$HY_PROBE" 2> /dev/null || true)
    if [[ "$hy" == "$HY_WANT" ]]; then
        echo "  ✓ hygiene live end to end (zshenv chain sourced env.sh under the flag)"
    else
        pf_fail+="zshenv chain did not produce the hygiene env (got '$hy'); "
    fi
    # -F pins the probe to the user config the Include lives in (and lets
    # tests point HOME elsewhere); defaults still apply for unset options
    sshg=$(ssh -F "$HOME/.ssh/config" -G github.com 2> /dev/null || true)
    if grep -q '^identityagent none$' <<< "$sshg" && grep -q '^batchmode yes$' <<< "$sshg"; then
        echo "  ✓ ssh Match live (identityagent none, batchmode yes)"
    else
        pf_fail+="ssh -G does not show the night Match in effect; "
    fi
    if [[ -n "$pf_fail" ]]; then
        command rm -f "$ACTIVE"
        die "post-flip verification failed — night NOT armed: ${pf_fail% }"
    fi
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
# keychain, shim): do not start a shift. DEGRADE = an optional channel is
# down (github-lease, linear-bot, sa-vault); the verdict names each one and
# a reduced night may still be worth arming. Verifies real channels and the
# enforcement machinery itself, never proxies. Properties that only exist
# while the flag is up (zshenv chain, the ssh Match) get their honest
# end-to-end check POST-FLIP in cmd_on; here they are checked as content
# and ORDER, the strongest pre-flip form (YUZ-88).
#
# Verdict discipline (YUZ-86): every probe records into its own CHANNEL
# register (ok/soft/bad take the channel as their first argument); the
# verdict is computed once, at the end, from the registers. No probe result
# gates another channel's probe, so an early failure cannot swallow a later
# classification — the ordered counter pair this replaces reported FAILED on
# every not-yet-minted evening, because the shim probe conflated "fnox
# wiring broken" with "lease absent" and its failure suppressed the
# DEGRADE branch entirely.
cmd_preflight() {
    local ch_failed='' ch_degraded=''
    ok() { echo "  ✓ $2"; } # CHANNEL MSG — channel named for uniformity
    soft() {
        echo "  ~ $2"
        [[ " $ch_degraded " == *" $1 "* ]] || ch_degraded+="$1 "
    }
    bad() {
        echo "  ✗ $2"
        [[ " $ch_failed " == *" $1 "* ]] || ch_failed+="$1 "
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
            ok hook "gate registered and executable"
        else
            bad hook "night-gate registered but not executable ($hookcmd)"
        fi
        if [[ "mcp__linear-server__get_issue" =~ $matcher ]]; then
            ok hook "PreToolUse matcher covers mcp__ tools"
        else
            bad hook "matcher '$matcher' does not cover mcp__ tools"
        fi
    else
        bad hook "night-gate not registered for PreToolUse in ~/.claude/settings.json"
    fi
    if jq -e '[.hooks.UserPromptSubmit[]? | select([.hooks[]?.command // empty] | any(test("night-gate")))] | length > 0' "$HOME/.claude/settings.json" > /dev/null 2>&1; then
        ok hook "user-prompt notifier registered (live sessions learn the flag fell)"
    else
        bad hook "night-gate user-prompt not registered for UserPromptSubmit"
    fi
    if [[ -n "$hookcmd" ]]; then
        local fire
        fire=$(printf '%s' '{"tool_name":"mcp__claude_ai_Gmail__get_message","tool_input":{}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if jq -e '(.hookSpecificOutput.permissionDecision == "deny") and (.hookSpecificOutput.permissionDecisionReason | test("connector"))' <<< "$fire" > /dev/null 2>&1; then
            ok hook "gate live-fire denies a connector tool (registered hook, forced on)"
        else
            bad hook "gate live-fire deny failed — the registered hook's deny path is broken"
        fi
        fire=$(printf '%s' '{"tool_name":"mcp__linear-server__list_issues","tool_input":{}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if [[ -z "$fire" ]]; then
            ok hook "gate live-fire allows Linear reads (silent)"
        else
            bad hook "gate live-fire allow path broken — allowed tool produced output"
        fi
    fi

    # hygiene channel: the deployed env.sh, really sourced in a clean shell,
    # must set the real values, not just exist under the right filename
    if [[ -f "$HOME/.claude/night/env.sh" ]]; then ok hygiene "env.sh present"; else bad hygiene "env.sh missing"; fi
    # non-commented wiring only: a commented-out guard once greened this
    # probe while delivering zero hygiene all night (YUZ-88). The chain
    # itself is re-verified end to end post-flip (cmd_on).
    if grep -qE '^[^#]*night/env\.sh' "$HOME/.zshenv" 2> /dev/null; then
        ok hygiene "zshenv guard wired (non-commented; chain re-verified post-flip)"
    else
        bad hygiene "no non-commented night/env.sh line in ~/.zshenv — hygiene would never load"
    fi
    local hy
    hy=$(env -i HOME="$HOME" PATH="$PATH" CLAUDECODE=1 zsh -c \
        "source '$HOME/.claude/night/env.sh'; $HY_PROBE" 2> /dev/null || true)
    if [[ "$hy" == "$HY_WANT" ]]; then
        ok hygiene "hygiene env sources clean: gh isolated, fnox headless, ssh agent dead, op sentinel armed, signing off, git-over-ssh killed"
    else
        bad hygiene "hygiene env content wrong when sourced (got '$hy')"
    fi
    # feedback layer, really exercised: credential-shaped bare commands must
    # die fast (not hang on anything lockable) with a readable error
    # the environment is NOT neutralized on purpose: gh prefers an env
    # token over any config dir, so a day GH_TOKEN in the inherited
    # environment IS the leak this probe exists to catch (YUZ-88)
    if command -v gh > /dev/null 2>&1; then
        if timeout 5 env GH_CONFIG_DIR="$HOME/.claude/night/gh-null" gh api user > /dev/null 2>&1; then
            bad hygiene "bare gh with the night config dir unexpectedly SUCCEEDED — day auth (an env GH_TOKEN outranks the config dir) would ride into night shells"
        else
            ok hygiene "bare gh dies fast under night gh isolation (inherited env, no token neutralization)"
        fi
    fi
    if command -v op > /dev/null 2>&1; then
        if timeout 5 env OP_SERVICE_ACCOUNT_TOKEN="ops_NIGHT_SENTINEL_invalid_on_purpose_use_night_op" op vault list > /dev/null 2>&1; then
            bad hygiene "op with the sentinel token unexpectedly SUCCEEDED"
        else
            ok hygiene "op dies fast on the sentinel token (no desktop-app hang path)"
        fi
    fi
    # ssh: the real property is ORDER — ssh keeps the FIRST value, so the
    # Include must precede any IdentityAgent directive or the (locked) day
    # agent stays the identity agent all night. Order is checkable while
    # the flag is down; the effective value is re-verified post-flip
    # (cmd_on runs ssh -G once the Match can fire). (YUZ-88)
    if [[ -f "$HOME/.claude/night/ssh-night.conf" ]]; then ok ssh "ssh-night.conf present"; else bad ssh "ssh-night.conf missing"; fi
    local inc_ln agent_ln
    inc_ln=$(grep -inEm1 -e '^[^#]*Include[[:space:]].*night/ssh-night\.conf' "$HOME/.ssh/config" 2> /dev/null | cut -d: -f1 || true)
    agent_ln=$(grep -inEm1 -e '^[[:space:]]*IdentityAgent[[:space:]]' "$HOME/.ssh/config" 2> /dev/null | cut -d: -f1 || true)
    if [[ -z "$inc_ln" ]]; then
        bad ssh "no non-commented Include of night/ssh-night.conf in \$HOME/.ssh/config"
    elif [[ -n "$agent_ln" ]] && ((agent_ln < inc_ln)); then
        bad ssh "IdentityAgent at line $agent_ln precedes the night Include (line $inc_ln) — first-value-wins keeps the day agent all night"
    else
        ok ssh "night Include precedes any IdentityAgent (order verified; effective value re-checked post-flip)"
    fi
    # regression: on-disk private keys would let night ssh succeed as the owner
    if grep -lE -- '-----BEGIN .*PRIVATE KEY' "$HOME"/.ssh/* 2> /dev/null | grep -q .; then
        bad ssh "private key files on disk in ~/.ssh — night ssh could succeed with the owner's identity"
    else
        ok ssh "no on-disk ssh private keys"
    fi
    # file-tool writes to the queue/report dirs must not prompt at night
    if "$HOME/.claude/hooks/path-gate.sh" check "$NIGHT_DIR" w > /dev/null 2>&1; then
        ok path-gate "path-gate grants w on $NIGHT_DIR (queue + report writes)"
    else
        bad path-gate "path-gate denies w on $NIGHT_DIR — queue/report writes would prompt with nobody awake"
    fi

    # credential channel: real resolutions, not existence checks
    if [[ -x "$NIGHT_SHIM" ]]; then ok fabric "night shim executable"; else bad fabric "night shim missing"; fi
    if command -v fnox > /dev/null 2>&1; then
        ok fabric "fnox present ($(fnox --version 2> /dev/null || echo '?'))"
    else
        bad fabric "fnox not installed"
    fi
    # the shim pins its config chain to this exact file (cwd-independence);
    # missing means every night invocation fails identically
    if [[ -f "$HOME/.config/fnox/config.toml" ]]; then
        ok fabric "global fnox config present (the shim's pinned -c)"
    else
        bad fabric "global fnox config missing at ~/.config/fnox/config.toml"
    fi
    # lease-independent on purpose: `fnox get` resolves the single secret, so
    # a dead GitHub lease cannot mask a healthy (or broken) 1Password channel
    local sa
    if ! command -v op > /dev/null 2>&1; then
        soft sa-vault "op absent — night vault channel unavailable on this machine (linux custody: YUZ-80)"
    elif sa=$(FNOX_NON_INTERACTIVE=1 fnox -c "$HOME/.config/fnox/config.toml" -P night get OP_SERVICE_ACCOUNT_TOKEN 2> /dev/null) \
        && OP_SERVICE_ACCOUNT_TOKEN="$sa" timeout 20 op vault list --format=json 2> /dev/null \
        | jq -e 'map(.name) == ["Night"]' > /dev/null 2>&1; then
        ok sa-vault "SA reaches exactly the night vault (keychain ACL + 1Password, end to end)"
    else
        bad sa-vault "SA cannot list exactly the night vault (keychain ACL? dead token?)"
    fi

    # The shim probe, classified by ERROR TYPE: fnox exec resolves the lease
    # eagerly, so `night true` cannot tell "wiring broken" from "lease not
    # minted yet" by exit code alone. fnox's auth_failed diagnostic marks the
    # probe reaching lease resolution — that IS the fabric working; the
    # lease's own verdict lands in its own channel below.
    local shim_err='' shim_rc=0 lease_state=''
    shim_err=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" true 2>&1 > /dev/null) || shim_rc=$?
    if ((shim_rc == 0)); then
        ok fabric "night shim execs end to end (fnox exec path)"
        lease_state=alive
    elif grep -qE 'auth_failed|interactive auth required' <<< "$shim_err"; then
        ok fabric "night shim execs to the lease boundary (config parsed, lease consulted)"
        lease_state=absent
    else
        bad fabric "night shim cannot exec a trivial command — shim/fnox wiring broken: $(head -n 1 <<< "$shim_err")"
    fi

    # github-lease: absent => DEGRADE (local-only night possible), never FAIL
    local login
    case "$lease_state" in
        alive)
            if login=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user -q .login 2> /dev/null); then
                ok github-lease "github lease alive (as $login)"
                # T2 rests entirely on the App's permission set staying
                # contents:write (+ implicit metadata:read). Assert it from
                # the same response that names the repos: a quietly widened
                # App (pull_requests:write) would turn T2 403s into
                # successes and nothing else would ever notice. (YUZ-88)
                local inst_json widened
                inst_json=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user/installations 2> /dev/null || true)
                widened=$(jq -r '[.installations[].permissions | to_entries[]
                        | select((.key == "contents" and .value == "write") or (.key == "metadata" and .value == "read") | not)
                        | "\(.key):\(.value)"] | unique | join(", ")' <<< "$inst_json" 2> /dev/null) \
                    || widened="(unparseable user/installations response)"
                if [[ -z "$widened" ]]; then
                    ok github-lease "App permissions are exactly contents:write (+ metadata:read) — T2's 403s stand"
                else
                    bad github-lease "night App permissions beyond contents:write: $widened — a PR/issue write would SUCCEED. Fix the App before arming."
                fi
                echo "  scope — installation repositories (the enforced allowlist):"
                jq -r '.installations[].id' <<< "$inst_json" 2> /dev/null \
                    | while read -r iid; do
                        FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api "user/installations/$iid/repositories" --jq '.repositories[].full_name' 2> /dev/null | sed 's/^/    - /'
                    done || true
            else
                soft github-lease "lease resolves locally but GitHub rejects it (revoked? offline?) — re-mint at arm time"
            fi
            ;;
        absent)
            soft github-lease "github lease absent/expired — mint at arm time (one device-flow click), or arm --mode local-only"
            ;;
    esac

    # linear-bot: night task-state writes carry the agent identity; the gate
    # DENIES owner-identity writes and points at the bot connection, so an
    # absent token or connection means no tracker writes all night — its own
    # channel, never conflated with the GitHub lease (YUZ-90)
    local conn=unknown exp days
    if command -v claude > /dev/null 2>&1; then
        if claude mcp get linear-bot > /dev/null 2>&1; then conn=present; else conn=absent; fi
    fi
    if [[ ! -f "$NIGHT_DIR/linear-bot-expiry" ]]; then
        soft linear-bot "never minted (no expiry record) — task-state writes would have no identity to ride: run linear-bot-remint (day, presence-gated)"
    else
        exp=$(cat "$NIGHT_DIR/linear-bot-expiry" 2> /dev/null || echo 0)
        [[ "$exp" =~ ^[0-9]+$ ]] || exp=0
        # seconds first: integer division truncates toward zero, so a
        # freshly expired token would otherwise read "expires in 0d"
        local left
        left=$((exp - $(date +%s)))
        days=$((left / 86400))
        if ((left < 0)); then
            soft linear-bot "token expired — run linear-bot-remint (day, presence-gated)"
        elif ((days < 5)); then
            soft linear-bot "token expires in ${days}d — run linear-bot-remint before a long stretch"
        elif [[ "$conn" == absent ]]; then
            soft linear-bot "token alive (${days}d) but the linear-bot MCP connection is missing — linear-bot-remint re-wires it without minting"
        elif [[ "$conn" == present ]]; then
            ok linear-bot "linear-bot wired (token ${days}d left, MCP connection present)"
        else
            ok linear-bot "linear-bot token alive (${days}d left; claude CLI not visible — connection unchecked)"
        fi
    fi

    # verdict: computed HERE and only here, from the channel registers
    echo
    if [[ -n "$ch_failed" ]]; then
        echo "preflight FAILED — broken: ${ch_failed% }. Do not start the shift."
        exit 1
    fi
    if [[ -n "$ch_degraded" ]]; then
        echo "preflight DEGRADED — reduced: ${ch_degraded% }. Consequences, per channel:"
        [[ " $ch_degraded " == *" github-lease "* ]] \
            && echo "  github-lease: no night push/gh/op unless minted at arm time — or arm --mode local-only"
        [[ " $ch_degraded " == *" linear-bot "* ]] \
            && echo "  linear-bot: task-state writes fail ALL NIGHT — run linear-bot-remint BEFORE arming"
        [[ " $ch_degraded " == *" sa-vault "* ]] \
            && echo "  sa-vault: night op vault reads unavailable on this machine"
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
