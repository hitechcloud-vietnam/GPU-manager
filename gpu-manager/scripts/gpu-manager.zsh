#compdef gpu-manager main.py

_gpu_manager() {
    local -a commands
    commands=(
        'set:Configure GPU mode (mig, vgpu, passthrough)'
        'get:Get GPU status and readiness'
        'create:Create vGPU or MIG mdev instance'
        'delete:Delete mdev instance or GPU resources'
        'reset:Reset GPU resources'
        'status:Show GPU status'
        'profiles:List GPU profiles and capacity'
        'completion:Generate shell autocompletion script'
    )
    _describe -t commands 'gpu-manager command' commands
}

_gpu_manager "$@"
