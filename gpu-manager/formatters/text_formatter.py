from .base import BaseFormatter

class TextFormatter(BaseFormatter):
    def format_status(self, data: dict) -> str:
        lines = []
        if data.get("status") != "success":
            return f"[ERROR] {data.get('message', 'Unknown error')}"

        for node in data.get("gpu_nodes", []):
            lines.append("=" * 88)
            lines.append(f"GPU {node['index']} [{node['bus_id']}] - {node['model']} (MIG Mode: {node['mig_mode']})")
            lines.append(f"Temp: {node.get('temperature_c', 'N/A')}C | Power: {node.get('power_draw_w', 'N/A')}W / {node.get('power_limit_w', 'N/A')}W")
            lines.append(f"Memory: Used {node.get('used_memory_mb', 0)}MB / Total {node.get('total_memory_mb', 0)}MB (Free: {node.get('free_memory_mb', 0)}MB)")
            lines.append("-" * 88)
            
            # MIG 인스턴스
            mig_insts = node.get("mig_instances", [])
            if mig_insts:
                lines.append("  [MIG Instances]")
                for mig in mig_insts:
                    lines.append(f"  - GI ID: {mig['gpu_instance_id']}, CI ID: {mig['compute_instance_id']}, Profile: {mig['mig_profile']}")
            else:
                lines.append("  [MIG Instances] None")

            # mdev 장치
            mdevs = node.get("mdev_devices", [])
            if mdevs:
                lines.append("  [mdev Devices]")
                for mdev in mdevs:
                    pers = "Yes (--auto)" if mdev.get("persistent") else "No"
                    lines.append(f"  - UUID       : {mdev['uuid']}")
                    lines.append(f"    Profile    : {mdev['profile_name']} (Type: {mdev['type_id']})")
                    lines.append(f"    sysfs Path : {mdev['sysfs_path']}")
                    lines.append(f"    Persistent : {pers}")
            else:
                lines.append("  [mdev Devices] None")
        lines.append("=" * 88)
        return "\n".join(lines)

    def format_profiles(self, data: dict) -> str:
        lines = []
        if data.get("status") != "success":
            return f"[ERROR] {data.get('message', 'Unknown error')}"

        for node in data.get("gpu_nodes", []):
            lines.append("=" * 96)
            lines.append(f"GPU {node['index']} [{node['bus_id']}] - {node['model']} (MIG Mode: {node['mig_mode']})")
            slice_info_str = ""
            if node.get("mig_mode", "").lower() == "enabled" and "total_slices" in node:
                slice_info_str = f" | Total Slices: {node['total_slices']} (Free: {node['free_slices']})"
            lines.append(f"Total Memory: {node['total_memory_mb']}MB | Free Memory: {node['free_memory_mb']}MB{slice_info_str}")
            lines.append("=" * 96)
            lines.append(f"{'PROFILE NAME':<20} {'TYPE ID':<12} {'SLICES':<9} {'MEMORY':<10} {'MAX':<6} {'CREATED':<8} {'REMAINING CAPACITY'}")
            lines.append("-" * 96)
            for p in node.get("supported_profiles", []):
                rem_str = f"{p['remaining_capacity']}"
                if p['remaining_capacity'] == 0 and p['reason']:
                    rem_str += f" ({p['reason']})"
                ns_val = p.get('num_slices', 'N/A')
                ns_str = f"{ns_val} Slice" if isinstance(ns_val, int) and ns_val == 1 else (f"{ns_val} Slices" if isinstance(ns_val, int) else str(ns_val))
                lines.append(f"{p['profile_name']:<20} {p['type_id']:<12} {ns_str:<9} {p['memory_mb']}MB{'':<4} {p['max_instances']:<6} {p['created_count']:<8} {rem_str}")
            lines.append("=" * 96)
        return "\n".join(lines)

    def format_action_result(self, data: dict) -> str:
        if data.get("status") == "success":
            return f"[SUCCESS] {data.get('message', 'Action completed successfully.')}"
        else:
            return f"[ERROR] {data.get('message', 'Action failed.')}"

    def format_doctor(self, data: dict) -> str:
        lines = []
        lines.append("=" * 88)
        lines.append(f"GPU-Manager Host Virtualization Health Doctor Report (Overall: {data.get('overall_health', 'UNKNOWN')})")
        lines.append("=" * 88)
        for c in data.get("checks", []):
            st = c.get("status", "UNKNOWN")
            symbol = "[OK]  " if st == "OK" else (" [WARN]" if st == "WARN" else "[FAIL]")
            lines.append(f"{symbol} {c.get('name', 'Item'):<30} : {c.get('details', '')}")
        lines.append("=" * 88)
        if data.get("actions_taken"):
            lines.append("Actions Executed (--fix):")
            for act in data.get("actions_taken"):
                lines.append(f"  - {act}")
            lines.append("=" * 88)
        return "\n".join(lines)
