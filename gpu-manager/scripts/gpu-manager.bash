# gpu-manager Bash Shell Autocompletion script

_gpu_manager_completions() {
    local cur prev words cword
    _init_completion || return

    local commands="set get create delete reset doctor check status profiles mig vgpu mdev completion"


    if [[ ${cword} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${commands}" -- "${cur}") )
        return 0
    fi

    case "${words[1]}" in
        set)
            if [[ "${prev}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -W "mig vgpu passthrough --gpu --mig --vgpu --passthrough" -- "${cur}") )
            fi
            ;;
        get|status|profiles)
            if [[ "${prev}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --output -o -d" -- "${cur}") )
            fi
            ;;
        create)
            if [[ "${prev}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1" -- "${cur}") )
            elif [[ "${prev}" == "--profile" ]]; then
                # Dynamically fetch available profiles with remaining capacity > 0
                local profs=$(gpu-manager profiles -o json 2>/dev/null | grep -o '"profile_name": "[^"]*"' | cut -d'"' -f4 | sort -u)
                COMPREPLY=( $(compgen -W "${profs}" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --profile --count --uuid --persistent --non-persistent" -- "${cur}") )
            fi
            ;;
        delete)
            if [[ "${prev}" == "--uuid" ]]; then
                # Dynamically fetch active mdev UUIDs
                local uuids=$(gpu-manager status -o json 2>/dev/null | grep -o '"uuid": "[^"]*"' | cut -d'"' -f4)
                COMPREPLY=( $(compgen -W "${uuids}" -- "${cur}") )
            elif [[ "${prev}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -W "--uuid --gpu --all" -- "${cur}") )
            fi
            ;;
        reset)
            if [[ "${prev}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --all" -- "${cur}") )
            fi
            ;;
    esac
}

complete -F _gpu_manager_completions gpu-manager main.py
