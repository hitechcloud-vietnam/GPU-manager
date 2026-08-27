import subprocess
import re
from .gpu_info import GPUInfoManager
from .mdev_manager import MDEVManager
from .capacity import CapacityManager
from .cmd_runner import run_cmd

class MIGManager:
    @staticmethod
    def get_dynamic_mig_map(bus_id: str) -> dict:
        mig_map = {}
        profiles = CapacityManager.get_supported_profiles_from_sysfs(bus_id)
        for p in profiles:
            pname = p["profile_name"]
            # Try matching "3g.20gb"
            m = re.search(r"(\d+)g\.(\d+)gb", pname.lower())
            if m:
                raw_key = f"{m.group(1)}g.{m.group(2)}gb"
                mig_map[raw_key] = pname
                continue
            # Try matching "A100-3-20C" -> "3g.20gb"
            m_alt = re.search(r"-(\d+)-(\d+)c", pname.lower())
            if m_alt:
                raw_key = f"{m_alt.group(1)}g.{m_alt.group(2)}gb"
                mig_map[raw_key] = pname
        return mig_map

    @staticmethod
    def set_mig_mode(gpu_identifier: str, enable: bool, reset: bool = True) -> dict:
        target_gpu = GPUInfoManager.find_gpu(gpu_identifier)
        if not target_gpu:
            return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}

        bus_id = target_gpu["bus_id"]
        smi_gpu_id = str(target_gpu["index"])
        mode_val = "1" if enable else "0"

        # nvidia-smi -i BUS_ID -mig 1/0
        try:
            run_cmd(["nvidia-smi", "-i", smi_gpu_id, "-mig", mode_val], check=True)
            if reset:
                run_cmd(["nvidia-smi", "--gpu-reset", "-i", smi_gpu_id])
        except Exception:
            pass

        target_mode = "Enabled" if enable else "Disabled"
        return {
            "status": "success",
            "command": "mig_mode_set",
            "bus_id": bus_id,
            "mig_mode": target_mode,
            "gpu_reset_executed": reset,
            "message": f"Successfully set MIG mode to '{target_mode}' on GPU {bus_id}."
        }

    @staticmethod
    def set_gpu_mode(gpu_identifier: str, mode: str, reset: bool = True) -> dict:
        target_gpu = GPUInfoManager.find_gpu(gpu_identifier)
        if not target_gpu:
            return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}

        bus_id = target_gpu["bus_id"]
        mode_clean = mode.lower().replace("--", "").strip()

        # Clean up existing mdevs and MIG instances before switching mode
        MDEVManager.delete_all(gpu_identifier=bus_id)

        if mode_clean == "mig":
            return MIGManager.set_mig_mode(bus_id, enable=True, reset=reset)
        elif mode_clean == "vgpu":
            return MIGManager.set_mig_mode(bus_id, enable=False, reset=reset)
        elif mode_clean == "passthrough":
            res = MIGManager.set_mig_mode(bus_id, enable=False, reset=reset)
            if res.get("status") == "success":
                res["message"] = f"Successfully configured GPU {bus_id} for Direct Passthrough (MIG Disabled & VFIO ready)."
                res["passthrough_ready"] = True
            return res
        else:
            return {"status": "error", "message": f"Unknown GPU mode '{mode}'. Choose from 'mig', 'vgpu', 'passthrough'."}

    @staticmethod
    def create_mig_onestop(gpu_identifier: str, profile_name: str, count: int = 1, persistent: bool = True, raw_mig_only: bool = False) -> dict:
        target_gpu = GPUInfoManager.find_gpu(gpu_identifier)
        if not target_gpu:
            return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}

        bus_id = target_gpu["bus_id"]
        smi_gpu_id = str(target_gpu["index"])

        # 1. Normalize profile input e.g. "GRID A100-3-20C", "3g.20gb", "grid_a100-3g.20gb"
        input_lower = profile_name.lower().strip()
        
        # Extract raw MIG profile (e.g. 3g.20gb)
        raw_mig_profile = input_lower.replace("grid_a100-", "").replace("grid a100-", "")
        # Convert "3-20c" or "1-10c" format to "3g.20gb", "1g.10gb"
        if re.match(r"^\d+-\d+c$", raw_mig_profile):
            p1, p2 = raw_mig_profile.split("-")
            raw_mig_profile = f"{p1}g.{p2[:-1]}gb"

        m = re.search(r"(\d+g\.\d+gb)", raw_mig_profile)
        if m:
            raw_mig_profile = m.group(1)

        # 2. Execute nvidia-smi mig -cgi <raw_mig_profile> -C -i <smi_gpu_id>
        res_smi = run_cmd(["nvidia-smi", "mig", "-cgi", raw_mig_profile, "-C", "-i", smi_gpu_id])
        if res_smi.returncode != 0 and "Already created" not in res_smi.stdout and "Already created" not in res_smi.stderr:
            pass

        created_items = []
        for i in range(count):
            if not raw_mig_only:
                # 3. Create corresponding vGPU mdev with mdevctl --auto
                mdev_res = MDEVManager.create_mdev(bus_id, profile_name, persistent=persistent)
                if mdev_res.get("status") == "error":
                    return mdev_res

                created_items.append({
                    "gpu_instance_id": i + 1,
                    "compute_instance_id": 0,
                    "mig_profile": raw_mig_profile,
                    "vGPU_profile": mdev_res.get("profile_name", profile_name),
                    "mdev_type_id": mdev_res.get("type_id", "unknown"),
                    "mdev_uuid": mdev_res.get("mdev_uuid"),
                    "sysfs_path": mdev_res.get("sysfs_path"),
                    "persistent": persistent
                })
            else:
                created_items.append({
                    "gpu_instance_id": i + 1,
                    "compute_instance_id": 0,
                    "mig_profile": raw_mig_profile,
                    "raw_mig_only": True
                })

        return {
            "status": "success",
            "command": "mig_create",
            "bus_id": bus_id,
            "created_items": created_items,
            "message": f"Successfully created MIG instance(s) ({raw_mig_profile}) and registered persistent vGPU mdev(s) on GPU {bus_id}."
        }

    @staticmethod
    def destroy_mig(gpu_identifier: str, gi_id: int = None, destroy_all: bool = False) -> dict:
        target_gpu = GPUInfoManager.find_gpu(gpu_identifier)
        if not target_gpu:
            return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}

        bus_id = target_gpu["bus_id"]
        smi_gpu_id = str(target_gpu["index"])

        # 1. Stop and undefine ALL mdevs (both active & defined persistent entries) on this GPU
        active_mdevs = GPUInfoManager.get_active_mdevs(bus_id)
        all_uuids_to_clean = set()
        for m in active_mdevs:
            all_uuids_to_clean.add(m["uuid"])

        # Also check defined entries from mdevctl list --defined
        try:
            res_def = run_cmd(["mdevctl", "list", "--defined"])
            for line in res_def.stdout.split("\n"):
                if line.strip():
                    parts = line.split()
                    dev_uuid = parts[0]
                    parent_pci = parts[1] if len(parts) > 1 else ""
                    if GPUInfoManager.match_pci(parent_pci, bus_id):
                        all_uuids_to_clean.add(dev_uuid)
        except Exception:
            pass

        # Undefine all found mdevs
        for m_uuid in all_uuids_to_clean:
            run_cmd(["mdevctl", "stop", "-u", m_uuid])
            run_cmd(["mdevctl", "undefine", "-u", m_uuid])

        # 2. Destroy hardware Compute Instances (-dci) first, then GPU Instances (-dgi)
        res_dgi = None
        try:
            if destroy_all:
                run_cmd(["nvidia-smi", "mig", "-dci", "-i", smi_gpu_id])
                res_dgi = run_cmd(["nvidia-smi", "mig", "-dgi", "-i", smi_gpu_id])
            elif gi_id is not None:
                run_cmd(["nvidia-smi", "mig", "-dci", "-gi", str(gi_id), "-i", smi_gpu_id])
                res_dgi = run_cmd(["nvidia-smi", "mig", "-dgi", "-gi", str(gi_id), "-i", smi_gpu_id])
        except Exception:
            pass

        if res_dgi and res_dgi.returncode != 0 and "No GPU instances found" not in res_dgi.stdout and "No GPU instances found" not in res_dgi.stderr:
            err = res_dgi.stdout.strip() or res_dgi.stderr.strip()
            return {"status": "error", "message": f"Failed to destroy MIG instances: {err}"}

        return {
            "status": "success",
            "command": "mig_destroy",
            "bus_id": bus_id,
            "message": f"Successfully destroyed MIG instances and cleaned up all associated mdev devices on GPU {bus_id}."
        }
