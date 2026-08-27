import uuid
import os
import re
import time
import subprocess
from .gpu_info import GPUInfoManager
from .capacity import CapacityManager
from .cmd_runner import run_cmd

class MDEVManager:
    @staticmethod
    def create_mdev(gpu_identifier: str, profile_name: str, custom_uuid: str = None, persistent: bool = True) -> dict:
        target_gpu = GPUInfoManager.find_gpu(gpu_identifier)
        if not target_gpu:
            return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}

        bus_id = target_gpu["bus_id"]
        mig_enabled = target_gpu["mig_mode"].lower() == "enabled"

        # Resolve type_id dynamically from host sysfs
        profiles_db = CapacityManager.get_supported_profiles_from_sysfs(bus_id)
        matched_profile = None
        for p in profiles_db:
            pname_norm = p["profile_name"].lower().replace(" ", "").replace("-", "")
            input_norm = profile_name.lower().replace(" ", "").replace("-", "")
            if pname_norm == input_norm or p["type_id"].lower() == profile_name.lower():
                matched_profile = p
                break

        if not matched_profile:
            return {"status": "error", "message": f"Profile '{profile_name}' is invalid or not supported on GPU '{bus_id}'."}

        # If target GPU is in MIG Mode, auto-ensure hardware MIG instance exists before starting mdev
        if mig_enabled:
            p_lower = profile_name.lower().replace("grid_a100-", "").replace("grid a100-", "").strip()
            # Convert "3-20c" format to "3g.20gb"
            m_c = re.match(r"^(\d+)-(\d+)c$", p_lower)
            if m_c:
                mig_profile_id = f"{m_c.group(1)}g.{m_c.group(2)}gb"
            else:
                m_g = re.search(r"(\d+g\.\d+gb)", p_lower)
                mig_profile_id = m_g.group(1) if m_g else p_lower

            # Use GPU index (e.g. "1") for nvidia-smi mig command
            smi_gpu_id = str(target_gpu["index"])
            res_smi = run_cmd(["nvidia-smi", "mig", "-cgi", mig_profile_id, "-C", "-i", smi_gpu_id])
            if res_smi.returncode != 0 and "Already created" not in res_smi.stdout and "Already created" not in res_smi.stderr:
                err_msg = res_smi.stdout.strip() or res_smi.stderr.strip() or "Insufficient GPU resources"
                return {"status": "error", "message": f"Hardware MIG instance creation failed: {err_msg}"}

        # Determine exact sysfs PCI folder name e.g. 0000:3b:00.4
        sysfs_pci_dir = GPUInfoManager.find_sysfs_mdev_dir(bus_id)
        if not sysfs_pci_dir or not os.path.exists(sysfs_pci_dir):
            return {
                "status": "error",
                "message": (
                    f"GPU '{bus_id}' has no available SR-IOV Virtual Function interface under '/sys/class/mdev_bus/'. "
                    "Please verify that SR-IOV VFs are enabled via '/usr/lib/nvidia/sriov-manage -e all'."
                )
            }

        target_pci_for_mdevctl = os.path.basename(sysfs_pci_dir)
        mdev_uuid = custom_uuid if custom_uuid else str(uuid.uuid4())
        type_id = matched_profile["type_id"]

        # mdevctl define -u UUID -p PARENT_PCI -t TYPE_ID [--auto]
        cmd_define = ["mdevctl", "define", "-u", mdev_uuid, "-p", target_pci_for_mdevctl, "-t", type_id]
        if persistent:
            cmd_define.append("--auto")

        res_define = run_cmd(cmd_define)
        if res_define.returncode != 0:
            return {"status": "error", "message": f"mdevctl define failed: {res_define.stderr.strip() or res_define.stdout.strip()}"}

        # mdevctl start -u UUID
        cmd_start = ["mdevctl", "start", "-u", mdev_uuid]
        res_start = run_cmd(cmd_start)
        if res_start.returncode != 0:
            return {"status": "error", "message": f"mdevctl start failed: {res_start.stderr.strip() or res_start.stdout.strip()}"}

        mdev_path = os.path.join("/sys/bus/mdev/devices", mdev_uuid)
        return {
            "status": "success",
            "command": "mdev_create",
            "bus_id": bus_id,
            "target_vf_pci": target_pci_for_mdevctl,
            "profile_name": matched_profile["profile_name"],
            "type_id": type_id,
            "mdev_uuid": mdev_uuid,
            "sysfs_path": mdev_path,
            "persistent": persistent,
            "message": f"Successfully created mdev device ({mdev_uuid}) on GPU {bus_id}."
        }

    @staticmethod
    def cleanup_orphan_gi(bus_id: str, smi_gpu_id: str):
        """Clean up hardware GI instances that have no active mdevs assigned"""
        try:
            # Wait 0.3s for kernel VFIO / sysfs mdev unbind cleanup
            time.sleep(0.3)

            active_mdevs = GPUInfoManager.get_active_mdevs(bus_id)
            
            # If all mdevs are gone on this GPU, destroy all GIs
            if len(active_mdevs) == 0:
                for _ in range(3):
                    run_cmd(["nvidia-smi", "mig", "-dci", "-i", smi_gpu_id])
                    res_g = run_cmd(["nvidia-smi", "mig", "-dgi", "-i", smi_gpu_id])
                    if res_g.returncode == 0 or "No GPU instances" in res_g.stdout or "No GPU instances" in res_g.stderr:
                        break
                    time.sleep(0.3)
                return

            mig_insts = GPUInfoManager.get_mig_instances(bus_id)
            if not mig_insts:
                return

            # If active mdev count < hardware GI count, clean up excess orphan GIs
            if len(active_mdevs) < len(mig_insts):
                for gi in reversed(mig_insts):
                    gi_id = gi.get("gpu_instance_id")
                    if gi_id is not None:
                        # Retry up to 3 times to account for kernel unbind delay
                        for _ in range(3):
                            run_cmd(["nvidia-smi", "mig", "-dci", "-gi", str(gi_id), "-i", smi_gpu_id])
                            res_dgi = run_cmd(["nvidia-smi", "mig", "-dgi", "-gi", str(gi_id), "-i", smi_gpu_id])
                            if res_dgi.returncode == 0 or "No GPU instances" in res_dgi.stdout or "No GPU instances" in res_dgi.stderr:
                                break
                            time.sleep(0.3)
                        
                        rem_mdevs = GPUInfoManager.get_active_mdevs(bus_id)
                        rem_gis = GPUInfoManager.get_mig_instances(bus_id)
                        if len(rem_mdevs) >= len(rem_gis):
                            break
        except Exception:
            pass

    @staticmethod
    def delete_mdev(mdev_uuid: str) -> dict:
        # Find which GPU this mdev belongs to before deleting
        target_gpu_bus = None
        target_gpu_index = None
        base_dir = "/sys/class/mdev_bus"
        if os.path.exists(base_dir):
            for entry in os.listdir(base_dir):
                vf_dir = os.path.join(base_dir, entry)
                if os.path.exists(os.path.join(vf_dir, mdev_uuid)):
                    target_gpu_bus = entry
                    break

        if target_gpu_bus:
            gpu_node = GPUInfoManager.find_gpu(target_gpu_bus)
            if gpu_node:
                target_gpu_index = str(gpu_node["index"])

        # 1. mdevctl stop -u UUID
        cmd_stop = ["mdevctl", "stop", "-u", mdev_uuid]
        run_cmd(cmd_stop)

        # 2. mdevctl undefine -u UUID
        cmd_undefine = ["mdevctl", "undefine", "-u", mdev_uuid]
        res_undefine = run_cmd(cmd_undefine)

        # 3. Auto-cleanup orphan hardware MIG instances
        if target_gpu_bus and target_gpu_index:
            MDEVManager.cleanup_orphan_gi(target_gpu_bus, target_gpu_index)

        return {
            "status": "success",
            "command": "mdev_delete",
            "mdev_uuid": mdev_uuid,
            "message": f"Successfully stopped, undefined mdev device ({mdev_uuid}) and cleaned up associated hardware MIG instances."
        }

    @staticmethod
    def delete_all(gpu_identifier: str = None) -> dict:
        gpus = GPUInfoManager.get_all_gpus()
        target_gpus = gpus
        if gpu_identifier and gpu_identifier != "all":
            found = GPUInfoManager.find_gpu(gpu_identifier)
            if not found:
                return {"status": "error", "message": f"GPU '{gpu_identifier}' not found."}
            target_gpus = [found]

        cleaned_uuids = []
        for g in target_gpus:
            bus_id = g["bus_id"]
            smi_gpu_id = str(g["index"])

            # 1. Undefine and stop all active & defined mdevs
            active = GPUInfoManager.get_active_mdevs(bus_id)
            for m in active:
                run_cmd(["mdevctl", "stop", "-u", m["uuid"]])
                run_cmd(["mdevctl", "undefine", "-u", m["uuid"]])
                cleaned_uuids.append(m["uuid"])

            try:
                res_def = run_cmd(["mdevctl", "list", "--defined"])
                for line in res_def.stdout.split("\n"):
                    if line.strip():
                        parts = line.split()
                        u_str = parts[0]
                        p_str = parts[1] if len(parts) > 1 else ""
                        if GPUInfoManager.match_pci(p_str, bus_id):
                            run_cmd(["mdevctl", "stop", "-u", u_str])
                            run_cmd(["mdevctl", "undefine", "-u", u_str])
                            cleaned_uuids.append(u_str)
            except Exception:
                pass

            # 2. Destroy hardware MIG instances if MIG mode Enabled
            if g.get("mig_mode", "").lower() == "enabled":
                time.sleep(0.3)
                for _ in range(3):
                    run_cmd(["nvidia-smi", "mig", "-dci", "-i", smi_gpu_id])
                    res_d = run_cmd(["nvidia-smi", "mig", "-dgi", "-i", smi_gpu_id])
                    if res_d.returncode == 0 or "No GPU instances" in res_d.stdout or "No GPU instances" in res_d.stderr:
                        break
                    time.sleep(0.3)

        gpu_desc = f"GPU {gpu_identifier}" if gpu_identifier and gpu_identifier != "all" else "all GPUs"
        return {
            "status": "success",
            "command": "mdev_delete_all",
            "cleaned_uuids": list(set(cleaned_uuids)),
            "message": f"Successfully destroyed all mdev devices and hardware MIG instances on {gpu_desc}."
        }
