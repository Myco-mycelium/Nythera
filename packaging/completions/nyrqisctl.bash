# bash completion for nyrqisctl — the Nyrqis operator CLI.
#
# Install: source this file from ~/.bashrc, or copy it to
# /etc/bash_completion.d/nyrqisctl (Debian/Ubuntu) and re-login.
#
#   sudo cp packaging/completions/nyrqisctl.bash /etc/bash_completion.d/

_nyrqisctl() {
    local cur prev words cword
    _init_completion -n = 2>/dev/null || {
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
    }

    local commands="ping status health containers"
    local containers_cmds="list run kill"
    local common_opts="--socket --health-socket --timeout --json -v --verbose --help"

    # complete option values
    case "$prev" in
        --socket|--health-socket)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return
            ;;
        --timeout)
            return
            ;;
    esac

    # containers subcommands
    if [[ ${COMP_WORDS[1]} == "containers" && $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "$containers_cmds" -- "$cur") )
        return
    fi

    # run subcommand options + command words
    if [[ ${COMP_WORDS[1]} == "containers" && ${COMP_WORDS[2]} == "run" ]]; then
        case "$prev" in
            --name|--capabilities|--memory|--pids)
                return
                ;;
            --socket|--health-socket)
                COMPREPLY=( $(compgen -f -- "$cur") )
                return
                ;;
        esac
        COMPREPLY=( $(compgen -W "--name --capabilities --network --memory --pids $common_opts" -- "$cur") )
        # fall back to command/file completion once options are done
        if [[ -z "$COMPREPLY" ]]; then
            _command_offset 1 2>/dev/null || COMPREPLY=( $(compgen -f -- "$cur") )
        fi
        return
    fi

    # top level: commands + options
    COMPREPLY=( $(compgen -W "$commands $common_opts" -- "$cur") )
}
complete -F _nyrqisctl nyrqisctl
