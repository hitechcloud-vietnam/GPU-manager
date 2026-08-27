import argparse
import sys
import os
from formatters.text_formatter import TextFormatter
from formatters.json_formatter import JsonFormatter
from formatters.xml_formatter import XmlFormatter
from modules.gpu_info import GPUInfoManager
from modules.capacity import CapacityManager
from modules.mdev_manager import MDEVManager
from modules.mig_manager import MIGManager
from modules.doctor import DoctorManager
from modules import cmd_runner


# Try importing argcomplete for shell tab completion
try:
    import argcomplete
except ImportError:
    argcomplete = None

# Dynamic Completer for available profiles (only profiles with remaining_capacity > 0)
class ProfileCompleter:
    def __call__(self, prefix, parsed_args, **kwargs):
        gpu_id = getattr(parsed_args, "gpu", "all")
        gpus = GPUInfoManager.get_all_gpus()
        target_gpus = gpus
        if gpu_id and gpu_id != "all":
            found = GPUInfoManager.find_gpu(gpu_id)
            if found:
                target_gpus = [found]

        available_profiles = set()
        for g in target_gpus:
            mdevs = GPUInfoManager.get_active_mdevs(g["bus_id"])
            profiles = CapacityManager.calculate_remaining(g, mdevs)
            for p in profiles:
                if p.get("remaining_capacity", 0) > 0:
                    available_profiles.add(p["profile_name"])

        return [p for p in sorted(available_profiles) if p.lower().startswith(prefix.lower())]

# Dynamic Completer for defined & active mdev UUIDs
class UUIDCompleter:
    def __call__(self, prefix, parsed_args, **kwargs):
        all_uuids = set()
        gpus = GPUInfoManager.get_all_gpus()
        for g in gpus:
            mdevs = GPUInfoManager.get_active_mdevs(g["bus_id"])
            for m in mdevs:
                all_uuids.add(m["uuid"])
        defined = GPUInfoManager.get_defined_mdevs()
        for u in defined.keys():
            all_uuids.add(u)
        return [u for u in sorted(all_uuids) if u.lower().startswith(prefix.lower())]

def get_formatter(output_type: str):
    fmt = output_type.lower()
    if fmt == "json":
        return JsonFormatter(pretty=False)
    elif fmt == "json-pretty":
        return JsonFormatter(pretty=True)
    elif fmt == "xml":
        return XmlFormatter(pretty=False)
    elif fmt == "xml-pretty":
        return XmlFormatter(pretty=True)
    else:
        return TextFormatter()

def handle_status(args):
    gpu_id = getattr(args, "gpu", "all")
    gpus = GPUInfoManager.get_all_gpus()
    target_gpus = []

    if gpu_id == "all":
        target_gpus = gpus
    else:
        norm_target = str(gpu_id).strip().lower()
        for g in gpus:
            if str(g["index"]) == norm_target or g["bus_id"].lower() == GPUInfoManager.normalize_pci(norm_target):
                target_gpus.append(g)
                break

    if not target_gpus:
        data = {"status": "error", "message": f"GPU '{gpu_id}' not found."}
    else:
        gpu_nodes = []
        for g in target_gpus:
            mdevs = GPUInfoManager.get_active_mdevs(g["bus_id"])
            migs = GPUInfoManager.get_mig_instances(g["bus_id"])
            node = dict(g)
            node["mdev_devices"] = mdevs
            node["mig_instances"] = migs
            gpu_nodes.append(node)
        data = {"status": "success", "gpu_nodes": gpu_nodes}

    formatter = get_formatter(args.output)
    print(formatter.format_status(data))

def handle_profiles(args):
    gpu_id = getattr(args, "gpu", "all")
    gpus = GPUInfoManager.get_all_gpus()
    target_gpus = []

    if gpu_id == "all":
        target_gpus = gpus
    else:
        norm_target = str(gpu_id).strip().lower()
        for g in gpus:
            if str(g["index"]) == norm_target or g["bus_id"].lower() == GPUInfoManager.normalize_pci(norm_target):
                target_gpus.append(g)
                break

    if not target_gpus:
        data = {"status": "error", "message": f"GPU '{gpu_id}' not found."}
    else:
        gpu_nodes = []
        for g in target_gpus:
            mdevs = GPUInfoManager.get_active_mdevs(g["bus_id"])
            profiles = CapacityManager.calculate_remaining(g, mdevs)
            node = dict(g)
            node["supported_profiles"] = profiles
            gpu_nodes.append(node)
        data = {"status": "success", "gpu_nodes": gpu_nodes}

    formatter = get_formatter(args.output)
    print(formatter.format_profiles(data))

def check_preflight_warnings():
    try:
        report = DoctorManager.check_all()
        fails = [c for c in report["checks"] if c["status"] == "FAIL"]
        warns = [c for c in report["checks"] if c["status"] == "WARN"]
        if fails or warns:
            sys.stderr.write("[WARNING] Host virtualization environment has unconfigured items:\n")
            for item in fails + warns:
                sys.stderr.write(f"  - [{item['status']}] {item['name']}: {item['details']}\n")
            sys.stderr.write("  Run 'gpu-manager doctor --fix' or 'gpu-manager check --fix' to resolve.\n\n")
    except Exception:
        pass

def handle_doctor(args):
    formatter = get_formatter(args.output)
    if getattr(args, "fix", False):
        res = DoctorManager.fix_all()
    else:
        res = DoctorManager.check_all()

    if hasattr(formatter, "format_doctor"):
        print(formatter.format_doctor(res))
    else:
        print(formatter.format_action_result(res))

def handle_set(args):
    check_preflight_warnings()
    formatter = get_formatter(args.output)
    mode = args.mode or ("mig" if args.mig else ("vgpu" if args.vgpu else ("passthrough" if args.passthrough else None)))
    if not mode:
        res = {"status": "error", "message": "Mode parameter is required. Specify 'mig', 'vgpu', or 'passthrough'."}
    else:
        res = MIGManager.set_gpu_mode(gpu_identifier=args.gpu, mode=mode, reset=not args.no_reset)
    print(formatter.format_action_result(res))

def handle_get(args):
    handle_status(args)

def handle_create(args):
    check_preflight_warnings()
    formatter = get_formatter(args.output)

    formatter = get_formatter(args.output)
    is_persistent = not getattr(args, "non_persistent", False)
    res = MDEVManager.create_mdev(
        gpu_identifier=args.gpu,
        profile_name=args.profile,
        custom_uuid=args.uuid,
        persistent=is_persistent
    )
    print(formatter.format_action_result(res))

def handle_delete(args):
    formatter = get_formatter(args.output)
    if args.uuid:
        res = MDEVManager.delete_mdev(mdev_uuid=args.uuid)
    elif args.all:
        gpu_id = args.gpu if args.gpu else "all"
        res = MDEVManager.delete_all(gpu_identifier=gpu_id)
    else:
        res = {"status": "error", "message": "Specify --uuid <UUID> or --all [--gpu <GPU_ID>] to delete."}
    print(formatter.format_action_result(res))

def handle_reset(args):
    formatter = get_formatter(args.output)
    gpu_id = args.gpu if args.gpu else "all"
    res = MDEVManager.delete_all(gpu_identifier=gpu_id)
    print(formatter.format_action_result(res))

def handle_mig(args):
    formatter = get_formatter(args.output)
    if args.mig_action == "enable":
        res = MIGManager.set_mig_mode(args.gpu, enable=True, reset=not args.no_reset)
    elif args.mig_action == "disable":
        res = MIGManager.set_mig_mode(args.gpu, enable=False, reset=not args.no_reset)
    elif args.mig_action == "create":
        is_persistent = not getattr(args, "non_persistent", False)
        res = MIGManager.create_mig_onestop(
            args.gpu,
            profile_name=args.profile,
            count=args.count,
            persistent=is_persistent,
            raw_mig_only=getattr(args, "raw_mig_only", False)
        )
    elif args.mig_action == "destroy":
        res = MIGManager.destroy_mig(args.gpu, gi_id=args.gi_id, destroy_all=args.all)
    else:
        res = {"status": "error", "message": "Unknown MIG action."}
    print(formatter.format_action_result(res))

def handle_vgpu(args):
    formatter = get_formatter(args.output)
    if args.vgpu_action == "create":
        is_persistent = not getattr(args, "non_persistent", False)
        res = MDEVManager.create_mdev(
            gpu_identifier=args.gpu,
            profile_name=args.profile,
            custom_uuid=args.uuid,
            persistent=is_persistent
        )
    elif args.vgpu_action == "delete":
        res = MDEVManager.delete_mdev(mdev_uuid=args.uuid)
    else:
        res = {"status": "error", "message": "Unknown vgpu action."}

    print(formatter.format_action_result(res))

def handle_mdev(args):
    formatter = get_formatter(args.output)
    if args.mdev_action == "create":
        is_persistent = not getattr(args, "non_persistent", False)
        res = MDEVManager.create_mdev(
            gpu_identifier=args.gpu,
            profile_name=args.profile,
            custom_uuid=args.uuid,
            persistent=is_persistent
        )
    elif args.mdev_action == "delete":
        res = MDEVManager.delete_mdev(mdev_uuid=args.uuid)
    else:
        res = {"status": "error", "message": "Unknown mdev action."}

    print(formatter.format_action_result(res))

def handle_completion(args):
    sh = (args.shell or "bash").lower()
    script_name = "gpu-manager"
    if sh == "bash":
        print(f"""# gpu-manager Bash completion
_gpu_manager_completions() {{
    local cur prev words cword
    _init_completion || return

    local commands="set get create delete reset status profiles mig vgpu mdev completion"
    if [[ ${{cword}} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${{commands}}" -- "${{cur}}") )
        return 0
    fi

    case "${{words[1]}}" in
        set)
            if [[ "${{prev}}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${{cur}}") )
            else
                COMPREPLY=( $(compgen -W "mig vgpu passthrough --gpu --mig --vgpu --passthrough" -- "${{cur}}") )
            fi
            ;;
        get|status|profiles)
            if [[ "${{prev}}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${{cur}}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --output -o -d" -- "${{cur}}") )
            fi
            ;;
        create)
            if [[ "${{prev}}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1" -- "${{cur}}") )
            elif [[ "${{prev}}" == "--profile" ]]; then
                local profs=$(gpu-manager profiles -o json 2>/dev/null | grep -o '"profile_name": "[^"]*"' | cut -d'"' -f4 | sort -u)
                COMPREPLY=( $(compgen -W "${{profs}}" -- "${{cur}}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --profile --count --uuid --persistent --non-persistent" -- "${{cur}}") )
            fi
            ;;
        delete)
            if [[ "${{prev}}" == "--uuid" ]]; then
                local uuids=$(gpu-manager status -o json 2>/dev/null | grep -o '"uuid": "[^"]*"' | cut -d'"' -f4)
                COMPREPLY=( $(compgen -W "${{uuids}}" -- "${{cur}}") )
            elif [[ "${{prev}}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${{cur}}") )
            else
                COMPREPLY=( $(compgen -W "--uuid --gpu --all" -- "${{cur}}") )
            fi
            ;;
        reset)
            if [[ "${{prev}}" == "--gpu" ]]; then
                COMPREPLY=( $(compgen -W "0 1 all" -- "${{cur}}") )
            else
                COMPREPLY=( $(compgen -W "--gpu --all" -- "${{cur}}") )
            fi
            ;;
    esac
}}
complete -F _gpu_manager_completions gpu-manager main.py
""")
    elif sh == "zsh":
        print(f"""#compdef gpu-manager main.py

_gpu_manager() {{
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
}}
_gpu_manager "$@"
""")

def main():
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("-o", "--output", choices=["text", "json", "json-pretty", "xml", "xml-pretty"], default="text", help="Output format")
    parent_parser.add_argument("-d", "--debug", action="store_true", help="Enable debug/verbose command logging")
    parent_parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug/verbose command logging")

    parser = argparse.ArgumentParser(prog="gpu-manager", description="NVIDIA A100 GPU KVM Manager", parents=[parent_parser])
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # 1. set
    p_set = subparsers.add_parser("set", help="Configure GPU mode (mig, vgpu, passthrough)", parents=[parent_parser])
    p_set.add_argument("--gpu", required=True, help="Target GPU Index or PCI Bus ID")
    p_set.add_argument("mode", nargs="?", choices=["mig", "vgpu", "passthrough"], help="GPU mode: mig, vgpu, or passthrough")
    p_set.add_argument("--mig", action="store_true", help="Enable MIG mode")
    p_set.add_argument("--vgpu", action="store_true", help="Enable Standard vGPU mode")
    p_set.add_argument("--passthrough", action="store_true", help="Enable Direct Passthrough mode")
    p_set.add_argument("--no-reset", action="store_true", help="Skip GPU reset")

    # 2. get
    p_get = subparsers.add_parser("get", help="Get GPU status and readiness report", parents=[parent_parser])
    p_get.add_argument("--gpu", default="all", help="Target GPU Index or PCI Bus ID")
    p_get.add_argument("--all", action="store_true", help="Show all GPUs")

    # 3. create
    p_create = subparsers.add_parser("create", help="Create vGPU or MIG mdev instance", parents=[parent_parser])
    p_create.add_argument("--gpu", required=True, help="Target GPU")
    p_crt_prof = p_create.add_argument("--profile", required=True, help="Profile name")
    p_crt_uuid = p_create.add_argument("--uuid", help="Custom UUID")
    p_create.add_argument("--count", type=int, default=1, help="Count")
    p_create.add_argument("--persistent", action="store_true", default=True, help="Enable mdevctl --auto (default)")
    p_create.add_argument("--non-persistent", action="store_true", help="Disable mdevctl --auto")

    # 4. delete
    p_delete = subparsers.add_parser("delete", help="Delete mdev instance or GPU resources", parents=[parent_parser])
    p_del_uuid = p_delete.add_argument("--uuid", help="mdev UUID to delete")
    p_delete.add_argument("--gpu", help="Target GPU identifier for --all")
    p_delete.add_argument("--all", action="store_true", help="Delete all instances")

    # 5. reset
    p_reset = subparsers.add_parser("reset", help="Reset GPU resources", parents=[parent_parser])
    p_reset.add_argument("--gpu", help="Target GPU identifier")
    p_reset.add_argument("--all", action="store_true", help="Reset all GPUs")

    # 6. completion
    p_completion = subparsers.add_parser("completion", help="Generate shell autocompletion script", parents=[parent_parser])
    p_completion.add_argument("shell", nargs="?", choices=["bash", "zsh"], default="bash", help="Target shell")

    # 7. doctor / check
    p_doctor = subparsers.add_parser("doctor", help="Check host virtualization prerequisites & auto-fix", parents=[parent_parser])
    p_doctor.add_argument("--fix", action="store_true", help="Automatically fix missing host virtualization prerequisites")

    p_check = subparsers.add_parser("check", help="Check host virtualization prerequisites & auto-fix", parents=[parent_parser])
    p_check.add_argument("--fix", action="store_true", help="Automatically fix missing host virtualization prerequisites")


    # Legacy Commands (Backward Compatible)
    p_status = subparsers.add_parser("status", help="Show GPU status", parents=[parent_parser])
    p_status.add_argument("--gpu", default="all", help="Target GPU Index or PCI Bus ID")

    p_profiles = subparsers.add_parser("profiles", help="List profiles and remaining capacity", parents=[parent_parser])
    p_profiles.add_argument("--gpu", default="all", help="Target GPU Index or PCI Bus ID")

    p_mig = subparsers.add_parser("mig", help="Manage MIG instances", parents=[parent_parser])
    mig_sub = p_mig.add_subparsers(dest="mig_action")
    
    p_mig_en = mig_sub.add_parser("enable", help="Enable MIG mode", parents=[parent_parser])
    p_mig_en.add_argument("--gpu", required=True, help="Target GPU")
    p_mig_en.add_argument("--no-reset", action="store_true", help="Skip GPU reset")

    p_mig_dis = mig_sub.add_parser("disable", help="Disable MIG mode", parents=[parent_parser])
    p_mig_dis.add_argument("--gpu", required=True, help="Target GPU")
    p_mig_dis.add_argument("--no-reset", action="store_true", help="Skip GPU reset")

    p_mig_crt = mig_sub.add_parser("create", help="One-stop create MIG and vGPU mdev", parents=[parent_parser])
    p_mig_crt.add_argument("--gpu", required=True, help="Target GPU")
    p_mig_crt.add_argument("--profile", required=True, help="MIG profile (e.g. 3g.20gb)")
    p_mig_crt.add_argument("--count", type=int, default=1, help="Count of instances")
    p_mig_crt.add_argument("--persistent", action="store_true", default=True, help="Enable mdevctl --auto (default)")
    p_mig_crt.add_argument("--non-persistent", action="store_true", help="Disable mdevctl --auto")
    p_mig_crt.add_argument("--raw-mig-only", action="store_true", help="Create MIG hardware instance only")

    p_mig_dst = mig_sub.add_parser("destroy", help="Destroy MIG instances", parents=[parent_parser])
    p_mig_dst.add_argument("--gpu", required=True, help="Target GPU")
    p_mig_dst.add_argument("--gi-id", type=int, help="Target GI ID")
    p_mig_dst.add_argument("--all", action="store_true", help="Destroy all MIG instances")

    p_vgpu = subparsers.add_parser("vgpu", help="Manage Standard vGPU devices (MIG Disabled)", parents=[parent_parser])
    vgpu_sub = p_vgpu.add_subparsers(dest="vgpu_action")

    p_vgpu_crt = vgpu_sub.add_parser("create", help="Create Standard vGPU mdev", parents=[parent_parser])
    p_vgpu_crt.add_argument("--gpu", required=True, help="Target GPU")
    p_vgpu_crt.add_argument("--profile", required=True, help="vGPU profile")
    p_vgpu_crt.add_argument("--uuid", help="Custom UUID")
    p_vgpu_crt.add_argument("--persistent", action="store_true", default=True, help="Enable mdevctl --auto")
    p_vgpu_crt.add_argument("--non-persistent", action="store_true", help="Disable mdevctl --auto")

    p_vgpu_del = vgpu_sub.add_parser("delete", help="Delete Standard vGPU mdev", parents=[parent_parser])
    p_vgpu_del.add_argument("--uuid", required=True, help="mdev UUID to delete")

    p_mdev = subparsers.add_parser("mdev", help="Manage Universal mdev devices", parents=[parent_parser])
    mdev_sub = p_mdev.add_subparsers(dest="mdev_action")

    p_mdev_crt = mdev_sub.add_parser("create", help="Create Universal mdev", parents=[parent_parser])
    p_mdev_crt.add_argument("--gpu", required=True, help="Target GPU")
    p_mdev_crt.add_argument("--profile", required=True, help="Profile name or type_id")
    p_mdev_crt.add_argument("--uuid", help="Custom UUID")
    p_mdev_crt.add_argument("--persistent", action="store_true", default=True, help="Enable mdevctl --auto")
    p_mdev_crt.add_argument("--non-persistent", action="store_true", help="Disable mdevctl --auto")

    p_mdev_del = mdev_sub.add_parser("delete", help="Delete mdev", parents=[parent_parser])
    p_mdev_del.add_argument("--uuid", required=True, help="mdev UUID to delete")

    # Bind Completers for argcomplete if available
    if argcomplete:
        p_crt_prof.completer = ProfileCompleter()
        p_del_uuid.completer = UUIDCompleter()
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    if args.debug or args.verbose:
        cmd_runner.DEBUG_MODE = True

    if args.command == "set":
        handle_set(args)
    elif args.command == "get":
        handle_get(args)
    elif args.command == "create":
        handle_create(args)
    elif args.command == "delete":
        handle_delete(args)
    elif args.command == "reset":
        handle_reset(args)
    elif args.command in ["doctor", "check"]:
        handle_doctor(args)
    elif args.command == "completion":
        handle_completion(args)

    elif args.command == "status":
        handle_status(args)
    elif args.command == "profiles":
        handle_profiles(args)
    elif args.command == "mig":
        handle_mig(args)
    elif args.command == "vgpu":
        handle_vgpu(args)
    elif args.command == "mdev":
        handle_mdev(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
