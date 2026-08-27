import os
import glob
import re
import subprocess
from .gpu_info import GPUInfoManager

class CapacityManager:
    @staticmethod
    def get_supported_profiles_from_sysfs(bus_id: str) -> list:
        base_dir = "/sys/class/mdev_bus"
        if not os.path.exists(base_dir):
            return []

        # Find ALL matching VF directories under /sys/class/mdev_bus/
        matched_vf_dirs = []
        for entry in sorted(os.listdir(base_dir)):
            if GPUInfoManager.match_pci(entry, bus_id):
                matched_vf_dirs.append(os.path.join(base_dir, entry))

        if not matched_vf_dirs:
            return []

        # Collect profiles and sum available_instances across all VFs
        type_data = {}
        for vf_dir in matched_vf_dirs:
            supp_dir = os.path.join(vf_dir, "mdev_supported_types")
            if not os.path.exists(supp_dir):
                continue

            for type_id in sorted(os.listdir(supp_dir)):
                tpath = os.path.join(supp_dir, type_id)
                name_file = os.path.join(tpath, "name")
                desc_file = os.path.join(tpath, "description")
                avail_file = os.path.join(tpath, "available_instances")

                if not os.path.exists(name_file) or not os.path.exists(desc_file):
                    continue

                try:
                    with open(name_file, 'r') as f:
                        pname = f.read().strip()
                    with open(desc_file, 'r') as f:
                        desc = f.read().strip()
                    
                    avail_cnt = 0
                    if os.path.exists(avail_file):
                        with open(avail_file, 'r') as f:
                            avail_cnt = int(f.read().strip())
                except Exception:
                    continue

                if type_id not in type_data:
                    mem_m = re.search(r"framebuffer=(\d+)M", desc)
                    max_m = re.search(r"max_instance=(\d+)", desc)
                    memory_mb = int(mem_m.group(1)) if mem_m else 0
                    max_inst = int(max_m.group(1)) if max_m else 1

                    type_data[type_id] = {
                        "profile_name": pname,
                        "type_id": type_id,
                        "memory_mb": memory_mb,
                        "max_instances": max_inst,
                        "available_instances": avail_cnt,
                        "description": desc
                    }
                else:
                    type_data[type_id]["available_instances"] += avail_cnt

        return list(type_data.values())

    @classmethod
    def calculate_remaining(cls, gpu_node: dict, active_mdevs: list) -> list:
        bus_id = gpu_node["bus_id"]
        free_mem = gpu_node.get("free_memory_mb", 40960)
        mig_enabled = gpu_node.get("mig_mode", "Disabled").lower() == "enabled"

        # 1. Dynamic inspection from host sysfs / mdevctl
        all_profiles = cls.get_supported_profiles_from_sysfs(bus_id)

        # 2. Filter profiles based on MIG Mode
        filtered_profiles = []
        if all_profiles:
            for p in all_profiles:
                pname = p["profile_name"]
                # MIG profiles contain e.g. "-1-", "-2-", "-3-", "-4-", "-7-" or "1g.", "2g."
                is_mig_profile = bool(re.search(r"-\d+-\d+c|grid_\w+-\d+g\.", pname.lower())) or "cme" in pname.lower()
                if mig_enabled and is_mig_profile:
                    filtered_profiles.append(p)
                elif not mig_enabled and not is_mig_profile:
                    filtered_profiles.append(p)
            profiles_db = filtered_profiles
        else:
            # Fallback
            if mig_enabled:
                profiles_db = [
                    {"profile_name": "grid_a100-1g.5gb",  "type_id": "nvidia-474", "memory_mb": 5120,  "max_instances": 7},
                    {"profile_name": "grid_a100-2g.10gb", "type_id": "nvidia-475", "memory_mb": 10240, "max_instances": 3},
                    {"profile_name": "grid_a100-3g.20gb", "type_id": "nvidia-476", "memory_mb": 20480, "max_instances": 2},
                    {"profile_name": "grid_a100-4g.20gb", "type_id": "nvidia-477", "memory_mb": 20480, "max_instances": 1},
                    {"profile_name": "grid_a100-7g.40gb", "type_id": "nvidia-478", "memory_mb": 40960, "max_instances": 1},
                ]
            else:
                profiles_db = [
                    {"profile_name": "grid_a100-4c",  "type_id": "nvidia-468", "memory_mb": 4096,  "max_instances": 10},
                    {"profile_name": "grid_a100-5c",  "type_id": "nvidia-469", "memory_mb": 5120,  "max_instances": 8},
                    {"profile_name": "grid_a100-8c",  "type_id": "nvidia-470", "memory_mb": 8192,  "max_instances": 5},
                    {"profile_name": "grid_a100-10c", "type_id": "nvidia-471", "memory_mb": 10240, "max_instances": 4},
                    {"profile_name": "grid_a100-20c", "type_id": "nvidia-472", "memory_mb": 20480, "max_instances": 2},
                    {"profile_name": "grid_a100-40c", "type_id": "nvidia-473", "memory_mb": 40960, "max_instances": 1},
                ]

        total_gpu_mem = gpu_node.get("total_memory_mb", 40960)

        # Count active instances, allocated memory, and used slices per profile
        created_counts = {}
        allocated_mdev_mem = 0
        mdev_used_slices = 0

        # Mapping of profile/type_id to memory_mb to calculate allocated_mdev_mem accurately
        profile_mem_map = {
            "nvidia-468": 4096, "nvidia-469": 5120, "nvidia-470": 8192, "nvidia-471": 10240,
            "nvidia-472": 20480, "nvidia-473": 40960, "nvidia-474": 5120, "nvidia-475": 10240,
            "nvidia-476": 20480, "nvidia-477": 20480, "nvidia-478": 40960, "nvidia-1053": 10240, "nvidia-706": 5120
        }

        for mdev in active_mdevs:
            tid = mdev.get("type_id")
            pname = mdev.get("profile_name", "")
            if tid:
                created_counts[tid] = created_counts.get(tid, 0) + 1
                # Deduct allocated VRAM memory
                m_bytes = profile_mem_map.get(tid, 0)
                if m_bytes == 0:
                    m_mb = re.search(r"(\d+)c|(\d+)gb", pname.lower())
                    if m_mb:
                        val = int(m_mb.group(1) or m_mb.group(2))
                        m_bytes = val * 1024 if val <= 40 else val
                allocated_mdev_mem += m_bytes

            # Extract slice count e.g. "GRID A100-3-20C" -> 3 slices
            m_slice = re.search(r"(\d+)g|a100-(\d+)-", pname.lower())
            if m_slice:
                s_val = int(m_slice.group(1) or m_slice.group(2))
                mdev_used_slices += s_val

        # Also factor in hardware GI instances when in MIG mode
        hardware_gi_slices = 0
        if mig_enabled:
            mig_insts = GPUInfoManager.get_mig_instances(bus_id)
            for gi in mig_insts:
                prof = gi.get("mig_profile", "")
                m_gi = re.search(r"(\d+)g", prof.lower())
                if m_gi:
                    hardware_gi_slices += int(m_gi.group(1))

        # Total used slices is max of active mdev slices and hardware GI slices
        total_used_slices = max(mdev_used_slices, hardware_gi_slices)

        # Calculate effective free memory by deducting created mdev VRAM
        effective_free_mem = max(0, total_gpu_mem - allocated_mdev_mem)

        # A100 Total GPU Slices = 7
        total_gpu_slices = 7
        free_slices = max(0, total_gpu_slices - total_used_slices)

        if mig_enabled:
            gpu_node["total_slices"] = total_gpu_slices
            gpu_node["free_slices"] = free_slices
        else:
            gpu_node["total_slices"] = "N/A"
            gpu_node["free_slices"] = "N/A"

        result = []
        for p in profiles_db:
            pname = p["profile_name"]
            tid = p["type_id"]
            created = created_counts.get(tid, 0)
            max_inst = p["max_instances"]
            mem_req = p["memory_mb"]

            if mig_enabled:
                # Extract required slices for this profile
                m_s = re.search(r"(\d+)g|a100-(\d+)-", pname.lower())
                req_slice = int(m_s.group(1) or m_s.group(2)) if m_s else 1

                slice_cap = free_slices // req_slice
                mem_cap = effective_free_mem // mem_req if mem_req > 0 else 0
                limit_cap = max(0, max_inst - created)
                
                remaining = min(limit_cap, slice_cap, mem_cap)

                reason = ""
                if remaining == 0:
                    if created >= max_inst:
                        reason = "Max instances reached"
                    elif free_slices < req_slice:
                        reason = "Insufficient GPU slices"
                    elif effective_free_mem < mem_req:
                        reason = "Insufficient GPU memory"
            else:
                mem_cap = effective_free_mem // mem_req if mem_req > 0 else 0
                limit_cap = max(0, max_inst - created)
                remaining = min(limit_cap, mem_cap)

                reason = ""
                if remaining == 0:
                    if created >= max_inst:
                        reason = "Max instances reached"
                    elif effective_free_mem < mem_req:
                        reason = "Insufficient GPU memory"

            result.append({
                "profile_name": pname,
                "type_id": tid,
                "num_slices": req_slice if mig_enabled else "N/A",
                "memory_mb": mem_req,
                "max_instances": max_inst,
                "created_count": created,
                "remaining_capacity": remaining,
                "reason": reason
            })
        return result
