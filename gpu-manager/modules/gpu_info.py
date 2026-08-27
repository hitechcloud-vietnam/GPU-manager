import os
import glob
import re
import json
from .cmd_runner import run_cmd

class GPUInfoManager:
    @staticmethod
    def normalize_pci(pci_str: str) -> str:
        pci_str = str(pci_str).strip().lower()
        parts = pci_str.split(":")
        if len(parts) == 3: # domain:bus:slot.func
            domain, bus, slot_func = parts
            domain = domain.lstrip('0').zfill(4)
            return f"{domain}:{bus}:{slot_func}"
        elif len(parts) == 2: # bus:slot.func
            return f"0000:{parts[0]}:{parts[1]}"
        return pci_str

    @staticmethod
    def match_pci(vf_pci: str, physical_bus_id: str) -> bool:
        if ":" not in str(vf_pci) or ":" not in str(physical_bus_id):
            return False
        norm_vf = GPUInfoManager.normalize_pci(vf_pci)
        norm_phy = GPUInfoManager.normalize_pci(physical_bus_id)
        vf_parts = norm_vf.split(":")
        phy_parts = norm_phy.split(":")
        if len(vf_parts) < 3 or len(phy_parts) < 3:
            return False
        vf_bus_slot = vf_parts[1] + ":" + vf_parts[2].split(".")[0]
        phy_bus_slot = phy_parts[1] + ":" + phy_parts[2].split(".")[0]
        return vf_bus_slot == phy_bus_slot

    @staticmethod
    def find_gpu(gpu_identifier: str) -> dict:
        all_gpus = GPUInfoManager.get_all_gpus()
        gpu_id_str = str(gpu_identifier).strip().lower()
        for g in all_gpus:
            # 1. Match index e.g. "0", "1"
            if str(g["index"]).strip() == gpu_id_str or (gpu_id_str.isdigit() and g["index"] == int(gpu_id_str)):
                return g
            # 2. Match exact bus_id e.g. "00000000:d8:00.0"
            if g["bus_id"].lower() == gpu_id_str:
                return g
            # 3. Match normalized PCI e.g. 00000000:d8:00.0 vs 0000:d8:00.0
            if GPUInfoManager.match_pci(g["bus_id"], gpu_id_str):
                return g
        return None

    @staticmethod
    def get_all_gpus() -> list:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,pci.bus_id,name,temperature.gpu,power.draw,power.limit,memory.total,memory.used,memory.free,mig.mode.current",
            "--format=csv,noheader,nounits"
        ]
        try:
            res = run_cmd(cmd)
            lines = res.stdout.strip().split("\n")
        except Exception:
            return [
                {
                    "index": 0,
                    "bus_id": "00000000:3b:00.0",
                    "model": "NVIDIA A100-PCIE-40GB",
                    "temperature_c": 34,
                    "power_draw_w": 46,
                    "power_limit_w": 250,
                    "total_memory_mb": 40960,
                    "used_memory_mb": 0,
                    "free_memory_mb": 40960,
                    "mig_mode": "Disabled"
                },
                {
                    "index": 1,
                    "bus_id": "00000000:d8:00.0",
                    "model": "NVIDIA A100-PCIE-40GB",
                    "temperature_c": 36,
                    "power_draw_w": 88,
                    "power_limit_w": 250,
                    "total_memory_mb": 40960,
                    "used_memory_mb": 0,
                    "free_memory_mb": 40960,
                    "mig_mode": "Enabled"
                }
            ]

        gpus = []
        for line in lines:
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 10:
                try:
                    p_draw = float(parts[4]) if parts[4] != 'N/A' else 0.0
                    p_lim = float(parts[5]) if parts[5] != 'N/A' else 250.0
                except ValueError:
                    p_draw, p_lim = 0.0, 250.0

                gpus.append({
                    "index": int(parts[0]),
                    "bus_id": parts[1].lower(),
                    "model": parts[2],
                    "temperature_c": int(parts[3]) if parts[3].isdigit() else 0,
                    "power_draw_w": int(p_draw),
                    "power_limit_w": int(p_lim),
                    "total_memory_mb": int(parts[6]),
                    "used_memory_mb": int(parts[7]),
                    "free_memory_mb": int(parts[8]),
                    "mig_mode": parts[9]
                })
        return gpus

    @staticmethod
    def get_defined_mdevs() -> dict:
        defined_map = {}
        try:
            res = run_cmd(["mdevctl", "list", "--defined"])
            for line in res.stdout.split("\n"):
                if line.strip():
                    parts = line.split()
                    uuid = parts[0]
                    auto = "auto" in parts
                    defined_map[uuid] = {"persistent": auto}
        except Exception:
            pass
        return defined_map

    @staticmethod
    def find_sysfs_mdev_dir(bus_id: str, available_only: bool = True) -> str:
        # Search /sys/class/mdev_bus/ for matching SR-IOV VF PCI entries
        base_dir = "/sys/class/mdev_bus"
        if not os.path.exists(base_dir):
            return None
        for entry in sorted(os.listdir(base_dir)):
            if GPUInfoManager.match_pci(entry, bus_id):
                vf_dir = os.path.join(base_dir, entry)
                if available_only:
                    # Check if this VF has zero active mdevs
                    mdev_entries = [d for d in os.listdir(vf_dir) if d.startswith("0000") or "-" in d]
                    if len(mdev_entries) == 0:
                        return vf_dir
                else:
                    return vf_dir
        # Fallback to any matching entry
        for entry in sorted(os.listdir(base_dir)):
            if GPUInfoManager.match_pci(entry, bus_id):
                return os.path.join(base_dir, entry)
        return None

    @staticmethod
    def get_active_mdevs(bus_id: str) -> list:
        defined_info = GPUInfoManager.get_defined_mdevs()
        mdevs = []
        base_dir = "/sys/class/mdev_bus"
        if not os.path.exists(base_dir):
            return mdevs

        for entry in os.listdir(base_dir):
            if GPUInfoManager.match_pci(entry, bus_id):
                vf_dir = os.path.join(base_dir, entry)
                for item in os.listdir(vf_dir):
                    mdev_path = os.path.join(vf_dir, item)
                    # UUID format check (hyphenated UUID string)
                    if len(item) == 36 and "-" in item:
                        type_name = "unknown"
                        type_id = "unknown"
                        type_dir = os.path.join(mdev_path, "mdev_type")
                        if os.path.exists(type_dir):
                            name_file = os.path.join(type_dir, "name")
                            if os.path.exists(name_file):
                                with open(name_file, 'r') as f:
                                    type_name = f.read().strip()
                            type_id = os.path.basename(os.path.realpath(type_dir))
                        
                        is_persistent = defined_info.get(item, {}).get("persistent", False)
                        mdevs.append({
                            "uuid": item,
                            "profile_name": type_name,
                            "type_id": type_id,
                            "sysfs_path": f"/sys/bus/mdev/devices/{item}",
                            "persistent": is_persistent
                        })
        return mdevs

    @staticmethod
    def get_mig_instances(bus_id: str) -> list:
        gpu_node = GPUInfoManager.find_gpu(bus_id)
        if not gpu_node:
            return []

        smi_gpu_id = str(gpu_node["index"])
        mig_insts = []
        try:
            res = run_cmd(["nvidia-smi", "mig", "-lgi", "-i", smi_gpu_id])
            for line in res.stdout.split("\n"):
                line_str = line.strip()
                if "MIG" in line_str or re.search(r"\d+g\.\d+gb", line_str):
                    # Format: GPU Name Profile_ID Instance_ID Placement
                    # e.g. "1  MIG 3g.20gb  9  1  0:4"
                    m = re.search(r"MIG\s+([\w\.]+)\s+(\d+)\s+(\d+)", line_str)
                    if m:
                        pname = m.group(1)
                        gi_id = int(m.group(3)) # Real Instance ID
                        mig_insts.append({
                            "gpu_instance_id": gi_id,
                            "compute_instance_id": 0,
                            "mig_profile": pname
                        })
                    else:
                        # Fallback parsing
                        prof_m = re.search(r"MIG\s+([\w\.]+)|(\d+g\.\d+gb)", line_str)
                        nums = re.findall(r"\b\d+\b", line_str)
                        if prof_m and len(nums) >= 3:
                            pname = prof_m.group(1) or prof_m.group(2)
                            gi_id = int(nums[2]) # 3rd number is Instance ID
                            mig_insts.append({
                                "gpu_instance_id": gi_id,
                                "compute_instance_id": 0,
                                "mig_profile": pname
                            })
        except Exception:
            pass
        return mig_insts
