#compdef nyrqisctl
# zsh completion for nyrqisctl — the Nyrqis operator CLI.
#
# Install: copy to a directory on your $fpath and run compinit, e.g.
#   mkdir -p ~/.zsh/completions
#   cp packaging/completions/nyrqisctl.zsh ~/.zsh/completions/_nyrqisctl
#   echo 'fpath=(~/.zsh/completions $fpath); autoload -U compinit; compinit' >> ~/.zshrc

_nyrqisctl() {
    local -a commands
    commands=(
        'ping:Ping the daemon (no auth beyond the transport checks)'
        'status:Daemon status (operator)'
        'health:Daemon health diagnostics (operator)'
        'containers:Manage the daemon containers'
    )
    local -a common_opts
    common_opts=(
        '--socket[daemon main service socket]:socket path:_files'
        '--health-socket[dedicated health-probe socket (ADR-0021)]:socket path:_files'
        '--timeout[CALL timeout in seconds]:seconds'
        '--json[print raw JSON reply]'
        '-v[verbose logging]'
        '--verbose[verbose logging]'
        '--help[show help]'
    )

    if (( CURRENT == 2 )); then
        _describe -t commands 'nyrqisctl command' commands
        _arguments -s $common_opts
        return
    fi

    case $words[2] in
        containers)
            local -a ccmds
            ccmds=('list:List the daemon containers' 'run:Spawn a container' 'kill:Terminate a container')
            if (( CURRENT == 3 )); then
                _describe -t commands 'containers command' ccmds
                return
            fi
            case $words[3] in
                run)
                    _arguments -s \
                        '--name[container name]:name' \
                        '--capabilities[comma-separated capabilities]:caps' \
                        '--network[own network namespace]' \
                        '--memory[memory limit MiB]:MiB' \
                        '--pids[PID limit]:pids' \
                        $common_opts \
                        '1:command word: _command_names -e' \
                        '*:command arg: _files'
                    ;;
                kill)
                    _arguments -s '1:container id' $common_opts
                    ;;
                list)
                    _arguments -s $common_opts
                    ;;
            esac
            ;;
        ping|status|health)
            _arguments -s $common_opts
            ;;
    esac
}

_nyrqisctl "$@"
