# shellcheck shell=sh
# Night hygiene environment: the single home for these facts.
# Sourced by ~/.zshenv for every Claude-spawned shell (CLAUDECODE=1) while
# ~/.claude/night/active.json exists. Hot for all sessions: every Bash tool
# command is a fresh zsh that re-reads zshenv. Transitive: every descendant
# process inherits.
#
# Everything here is an ENV VAR on purpose: the Claude shell snapshot is
# sourced AFTER zshenv and re-exports PATH, so PATH edits die in tool
# shells; env vars survive and reach descendants, including day-born
# sessions whose snapshot still carries the op-plugin gh/hcloud aliases.
# Credentials are NOT here (fnox night profile, per invocation via `night`).

export SSH_AUTH_SOCK=""
export GIT_TERMINAL_PROMPT=0
# Inherited overrides reopen whole classes the vars below close: an env
# GIT_SSH_COMMAND/GIT_SSH outranks core.sshCommand=/usr/bin/false (git
# would exec the day ssh), and gh prefers an env GH_TOKEN/GITHUB_TOKEN
# over the isolated config dir. Clear them; `night gh` still works (fnox
# exec injects its token into the child, after this ran). (YUZ-89)
unset GIT_SSH_COMMAND GIT_SSH GH_TOKEN GITHUB_TOKEN
# Night is headless: a dead lease must fail fast and classifiable, never poll
# a device-flow code nobody will enter. FNOX_NON_INTERACTIVE covers device
# and browser auth (FNOX_PROMPT_AUTH does not), and also blocks fnox's own
# <5min-to-expiry auto-remint (a device flow) from firing mid-shift.
# /night on mints interactively BEFORE the flag falls.
export FNOX_NON_INTERACTIVE=1
# gh physically isolated from any day login state: with no config dir there
# is nothing to fall back to when the night lease is absent, so gh fails
# fast.
export GH_CONFIG_DIR="$HOME/.claude/night/gh-null"
# Sentinel: a deliberately malformed 1Password service-account token. ANY
# op invocation takes the SA code path and dies in ~50ms on token parse
# ("format is invalid") instead of hanging on the locked desktop app. That
# covers every route to op: bare calls, the day op-plugin gh/hcloud aliases
# baked into a day-born session's snapshot, and calls nested inside
# scripts. `night op ...` still works: fnox exec overrides this with the
# real SA token.
export OP_SERVICE_ACCOUNT_TOKEN="ops_NIGHT_SENTINEL_invalid_on_purpose_use_night_op"
export GIT_CONFIG_COUNT=7
export GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_1=git@github.com:
export GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_2=ssh://git@github.com/
export GIT_CONFIG_KEY_3=credential.helper GIT_CONFIG_VALUE_3=
export GIT_CONFIG_KEY_4=credential.helper GIT_CONFIG_VALUE_4='!gh auth git-credential'
# The insteadOf pair above only covers github.com; user gitconfig may rewrite
# OTHER hosts https->ssh (url.<ssh>.insteadOf), and insteadOf longest-match
# cannot be neutralized per-host from here. Kill the whole class instead: any
# git that reaches ssh fails in milliseconds, not hanging on the locked
# 1Password agent until morning. (Bare ssh itself is handled in ssh_config:
# ~/.claude/night/ssh-night.conf, Include'd by the owner.)
export GIT_CONFIG_KEY_5=core.sshCommand GIT_CONFIG_VALUE_5=/usr/bin/false
export GIT_CONFIG_KEY_6=tag.gpgsign GIT_CONFIG_VALUE_6=false
