# vim:ft=zsh
#
# OSC 133 semantic prompt marks for zsh: lets a terminal find each prompt,
# command, and its output (jump to prompt, select output, exit status).
# Terminals that don't know OSC 133 ignore it, so this runs everywhere.
#
# Trimmed from Ghostty's zsh integration to its OSC 133 part only (no OSC 7
# cwd, title, cursor, sudo, or ssh), with _ghostty_ renamed to _osc133_.
#   upstream: https://github.com/ghostty-org/ghostty/blob/6eb24a0f0f38/src/shell-integration/zsh/ghostty-integration
# Ghostty's file started as a copy of kitty's zsh integration and is GPLv3,
# so this file is GPLv3 too:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Source it from .zshrc after the prompt theme is set up. Initialization is
# deferred to the first prompt, so later init code can still change PS1.

_osc133_entrypoint() {
    builtin emulate -L zsh -o no_warn_create_global -o no_aliases

    [[ -o interactive ]]     || builtin return 0  # non-interactive shell
    (( ! $+_osc133_state ))  || builtin return 0  # already initialized
    # The terminal's own integration already writes these marks.
    (( ! $+_ghostty_state && ! $+_ksi_state )) || builtin return 0

    builtin autoload -- is-at-least 2>/dev/null && is-at-least 5.1 || builtin return 0

    # 0: no OSC 133 [AC] marks have been written yet.
    # 1: the last written OSC 133 C has not been closed with D yet.
    # 2: none of the above.
    builtin typeset -gi _osc133_state

    # A close-on-exec fd to the TTY, so marks reach the terminal even when
    # stdout is redirected, without leaking an fd to child processes.
    # Opened here, not in deferred init, because some plugins close fds they
    # didn't open.
    typeset -gi _osc133_fd
    {
        builtin zmodload zsh/system && (( $+builtins[sysopen] )) && {
            { [[ -w     $TTY ]] && builtin sysopen -o cloexec -wu _osc133_fd --     $TTY } ||
            { [[ -w /dev/tty ]] && builtin sysopen -o cloexec -wu _osc133_fd -- /dev/tty }
        }
    } 2>/dev/null || (( _osc133_fd = 1 ))

    builtin typeset -ag precmd_functions
    precmd_functions+=(_osc133_deferred_init)
}

_osc133_deferred_init() {
    builtin emulate -L zsh -o no_warn_create_global -o no_aliases

    _osc133_precmd() {
        builtin local -i cmd_status=$?
        builtin emulate -L zsh -o no_warn_create_global -o no_aliases

        # Not from zle: some plugins run precmd hooks from zle to update the
        # prompt on cd, and D must close a real command.
        # Known limits: a precmd or chpwd hook that prints (pure prints an
        # empty line) makes D late, and with prompt_sp a command output that
        # doesn't end in LF claims the '%' as its own.
        if ! builtin zle; then
            if (( _osc133_state == 1 )); then
                builtin print -nu $_osc133_fd '\e]133;D;'$cmd_status'\a'
                (( _osc133_state = 2 ))
            elif (( _osc133_state == 2 )); then
                builtin print -nu $_osc133_fd '\e]133;D\a'
            fi
        fi

        builtin local mark1=$'%{\e]133;A;cl=line\a%}'
        if [[ -o prompt_percent ]]; then
            builtin typeset -g precmd_functions
            if [[ ${precmd_functions[-1]} == _osc133_precmd ]]; then
                # Last hook: marks in PS1/PS2 are redrawn with the prompt on
                # reset-prompt, SIGWINCH, and SIGCHLD.

                # Hand other hooks a clean PS1: restore it if nothing changed
                # it since we marked it; if a theme changed it, keep theirs.
                builtin local ps1_changed=0
                if [[ -n ${_osc133_saved_ps1+x} ]]; then
                    if [[ $PS1 == $_osc133_marked_ps1 ]]; then
                        PS1=$_osc133_saved_ps1
                        PS2=$_osc133_saved_ps2
                    elif [[ $PS1 != $_osc133_saved_ps1 ]]; then
                        ps1_changed=1
                    fi
                fi
                _osc133_saved_ps1=$PS1
                _osc133_saved_ps2=$PS2

                builtin local mark2=$'%{\e]133;P;k=s\a%}'
                builtin local markB=$'%{\e]133;B\a%}'
                # A trailing bare '%' would join markB's '{' into '%{'.
                [[ $PS1 == *[^%]% || $PS1 == % ]] && PS1=$PS1%
                PS1=${mark1}${PS1}${markB}

                # Mark continuation lines of a multiline prompt, unless a
                # theme rebuilt PS1 (marks in its newlines break themes that
                # pattern-match their own prompt, such as pure).
                if (( ! ps1_changed )) && [[ $PS1 == *$'\n'* ]]; then
                    PS1=${PS1//$'\n'/$'\n'${mark2}}
                fi

                [[ $PS2 == *[^%]% || $PS2 == % ]] && PS2=$PS2%
                PS2=${mark2}${PS2}${markB}

                _osc133_marked_ps1=$PS1
                (( _osc133_state = 2 ))
            else
                # Not last, so PS1 changes may not stick: move to the end for
                # next time, and print the mark directly this time.
                precmd_functions=(${precmd_functions:#_osc133_precmd} _osc133_precmd)
                if ! builtin zle; then
                    builtin print -rnu $_osc133_fd -- $mark1[3,-3]
                    (( _osc133_state = 2 ))
                fi
            fi
        elif ! builtin zle; then
            builtin print -rnu $_osc133_fd -- $mark1[3,-3]
            (( _osc133_state = 2 ))
        fi
    }

    _osc133_preexec() {
        builtin emulate -L zsh -o no_warn_create_global -o no_aliases

        # Hand other preexec hooks a clean PS1.
        if [[ -n ${_osc133_saved_ps1+x} && $PS1 == $_osc133_marked_ps1 ]]; then
            PS1=$_osc133_saved_ps1
            PS2=$_osc133_saved_ps2
        fi

        # Known limit: output of a preexec hook that runs before this one and
        # prints is marked as command output.
        builtin print -nu $_osc133_fd '\e]133;C\a'
        (( _osc133_state = 1 ))
    }

    # Mark the prompt at line-init when PS1 lost our marks, such as when a
    # plugin regenerated it after our precmd. P, not A: the prompt is already
    # drawn, and A's fresh-line would disturb it.
    (( $+functions[_osc133_zle_line_init] )) || _osc133_zle_line_init() { builtin true; }
    functions[_osc133_zle_line_init]="
        if [[ \$PS1 != *$'%{\\e]133;A'* ]]; then
            builtin print -nu \$_osc133_fd '\\e]133;P;k=i\\a\\e]133;B\\a'
        fi
    "${functions[_osc133_zle_line_init]}

    # Hook zle-line-init by chaining any existing widget: add-zle-hook-widget
    # breaks some user configs, so it is only used when it already owns the
    # widget. A manual `source ~/.zshrc` under oh-my-zsh drops this hook; use
    # `exec zsh` instead.
    builtin local widget=zle-line-init func=_osc133_zle_line_init orig_widget flag
    if [[ $widgets[$widget] == user:azhw:* &&
          $+functions[add-zle-hook-widget] -eq 1 ]]; then
        add-zle-hook-widget line-init $func
    else
        if (( $+widgets[$widget] )); then
            orig_widget=._osc133_orig_$widget
            builtin zle -A $widget $orig_widget
            if [[ $widgets[$widget] == user:* ]]; then
                flag=
            else
                flag=w
            fi
            functions[$func]+="
                builtin zle $orig_widget -N$flag -- \"\$@\""
        fi
        builtin zle -N $widget $func
    fi

    builtin typeset -ag preexec_functions
    preexec_functions+=(_osc133_preexec)

    builtin typeset -ag precmd_functions
    precmd_functions=(${precmd_functions:#_osc133_deferred_init} _osc133_precmd)
    _osc133_precmd

    builtin unfunction _osc133_deferred_init
}

_osc133_entrypoint
builtin unfunction _osc133_entrypoint
