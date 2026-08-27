import os
import subprocess
import shutil
from modules.cmd_runner import run_cmd

SRIOV_SERVICE_CONTENT = """[Unit]
Description=Enable NVIDIA GPU SR-IOV Virtual Functions at Boot
After=network.target nvidia-vgpud.service nvidia-vgpu-mgr.service
Before=mdevctl.service

[Service]
Type=oneshot
ExecStart=/usr/lib/nvidia/sriov-manage -e all
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

PERSISTENCED_SERVICE_CONTENT = """[Unit]
Description=NVIDIA Persistence Mode Enable Service Fallback
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/nvidia-smi -pm 1
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

class DoctorManager:
    @staticmethod
    def check_iommu() -> dict:
        cmdline_path = "/proc/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "r") as f:
                content = f.read()
            if "intel_iommu=on" in content or "amd_iommu=on" in content or "iommu=pt" in content:
                return {"status": "OK", "name": "IOMMU Virtualization", "details": "Intel VT-d / AMD IOMMU kernel option is enabled."}
        return {"status": "WARN", "name": "IOMMU Virtualization", "details": "IOMMU option not detected in /proc/cmdline. Ensure intel_iommu=on or amd_iommu=on in GRUB."}

    @staticmethod
    def check_vgpu_driver() -> dict:
        try:
            res = run_cmd(["nvidia-smi"])
            if res.returncode == 0 and os.path.exists("/sys/class/mdev_bus"):
                return {"status": "OK", "name": "NVIDIA GRID vGPU Driver", "details": "NVIDIA vGPU Manager Host Driver is running and sysfs mdev_bus is available."}
            elif res.returncode == 0:
                return {"status": "WARN", "name": "NVIDIA GRID vGPU Driver", "details": "nvidia-smi working, but /sys/class/mdev_bus not found. Verify GRID vGPU Host driver is installed instead of desktop driver."}
        except Exception as e:
            pass
        return {"status": "FAIL", "name": "NVIDIA GRID vGPU Driver", "details": "nvidia-smi command failed or NVIDIA driver not loaded."}

    @staticmethod
    def check_mdevctl() -> dict:
        if shutil.which("mdevctl"):
            return {"status": "OK", "name": "mdevctl Package", "details": "mdevctl tool is installed."}
        return {"status": "FAIL", "name": "mdevctl Package", "details": "mdevctl tool is not found. Install via 'dnf install mdevctl'."}

    @staticmethod
    def check_persistenced() -> dict:
        try:
            res = run_cmd(["systemctl", "is-active", "nvidia-persistenced"])
            if res.stdout and res.stdout.strip() == "active":
                return {"status": "OK", "name": "NVIDIA Persistence Daemon", "details": "nvidia-persistenced daemon service is active."}
            
            # Check nvidia-smi persistence mode if daemon not active
            res_pm = run_cmd(["nvidia-smi", "--query-gpu=persistence_mode", "--format=csv,noheader"])
            if res_pm.stdout and "Enabled" in res_pm.stdout:
                return {"status": "OK", "name": "NVIDIA Persistence Mode", "details": "Persistence Mode is Enabled via nvidia-smi -pm 1 (Fallback service active)."}
        except Exception:
            pass
        return {"status": "WARN", "name": "NVIDIA Persistence Daemon", "details": "nvidia-persistenced or Persistence Mode is inactive. Run 'gpu-manager doctor --fix' to create fallback service."}


    @staticmethod
    def check_vfio_module() -> dict:
        try:
            res = run_cmd(["lsmod"])
            if res.stdout and "nvidia_vgpu_vfio" in res.stdout:
                return {"status": "OK", "name": "NVIDIA VFIO Kernel Module", "details": "nvidia_vgpu_vfio kernel module is loaded."}
        except Exception:
            pass
        return {"status": "WARN", "name": "NVIDIA VFIO Kernel Module", "details": "nvidia_vgpu_vfio module not loaded. Run 'modprobe nvidia-vgpu-vfio'."}

    @staticmethod
    def check_vfio_conf() -> dict:
        conf_path = "/etc/modules-load.d/nvidia-vgpu-vfio.conf"
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f:
                content = f.read()
            if "nvidia-vgpu-vfio" in content:
                return {"status": "OK", "name": "VFIO Auto-load Conf", "details": f"{conf_path} configured."}
        return {"status": "WARN", "name": "VFIO Auto-load Conf", "details": f"{conf_path} missing or incomplete."}

    @staticmethod
    def check_vgpu_services() -> dict:
        try:
            res_d = run_cmd(["systemctl", "is-active", "nvidia-vgpud"])
            res_mgr = run_cmd(["systemctl", "is-active", "nvidia-vgpu-mgr"])
            d_active = res_d.stdout and res_d.stdout.strip() == "active"
            mgr_active = res_mgr.stdout and res_mgr.stdout.strip() == "active"
            if mgr_active or d_active:
                act_svc = "nvidia-vgpu-mgr" if mgr_active else "nvidia-vgpud"
                return {"status": "OK", "name": "vGPU Daemon Services", "details": f"vGPU Manager service ({act_svc}) is active."}
            return {"status": "WARN", "name": "vGPU Daemon Services", "details": f"nvidia-vgpud ({res_d.stdout.strip() if res_d.stdout else 'N/A'}), nvidia-vgpu-mgr ({res_mgr.stdout.strip() if res_mgr.stdout else 'N/A'}) inactive or missing."}
        except Exception:
            return {"status": "WARN", "name": "vGPU Daemon Services", "details": "nvidia-vgpud / nvidia-vgpu-mgr systemd services not found."}

    @staticmethod
    def check_sriov_service() -> dict:
        service_path = "/etc/systemd/system/nvidia-sriov.service"
        if os.path.exists(service_path):
            try:
                res = run_cmd(["systemctl", "is-enabled", "nvidia-sriov.service"])
                if res.stdout and res.stdout.strip() in ["enabled", "linked"]:
                    return {"status": "OK", "name": "NVIDIA SR-IOV Boot Service", "details": f"{service_path} exists and enabled."}
            except Exception:
                pass
            return {"status": "WARN", "name": "NVIDIA SR-IOV Boot Service", "details": f"{service_path} exists but not enabled."}
        return {"status": "WARN", "name": "NVIDIA SR-IOV Boot Service", "details": f"{service_path} does not exist."}


    @staticmethod
    def check_all() -> dict:
        checks = [
            DoctorManager.check_iommu(),
            DoctorManager.check_vgpu_driver(),
            DoctorManager.check_mdevctl(),
            DoctorManager.check_persistenced(),
            DoctorManager.check_vfio_module(),
            DoctorManager.check_vfio_conf(),
            DoctorManager.check_vgpu_services(),
            DoctorManager.check_sriov_service()
        ]
        has_fail = any(c["status"] == "FAIL" for c in checks)
        has_warn = any(c["status"] == "WARN" for c in checks)
        overall = "FAIL" if has_fail else ("WARN" if has_warn else "OK")

        return {
            "status": "success",
            "overall_health": overall,
            "checks": checks
        }


    @staticmethod
    def fix_all() -> dict:
        fix_results = []

        # 1. mdevctl
        if not shutil.which("mdevctl"):
            res = run_cmd(["dnf", "install", "-y", "mdevctl"])
            if res.returncode == 0:
                fix_results.append("Installed mdevctl package.")
            else:
                fix_results.append("Failed to install mdevctl via dnf.")

        # 2. nvidia-persistenced / fallback service
        res_pers = run_cmd(["systemctl", "enable", "--now", "nvidia-persistenced"])
        if res_pers.returncode != 0 or "not found" in (res_pers.stderr or "").lower():
            pers_service_path = "/etc/systemd/system/nvidia-persistenced.service"
            try:
                with open(pers_service_path, "w") as f:
                    f.write(PERSISTENCED_SERVICE_CONTENT)
                run_cmd(["systemctl", "daemon-reload"])
                run_cmd(["systemctl", "enable", "--now", "nvidia-persistenced.service"])
                fix_results.append(f"Created fallback {pers_service_path} to automatically run 'nvidia-smi -pm 1' at boot.")
            except Exception as e:
                fix_results.append(f"Failed creating fallback persistence service: {e}")
        else:
            fix_results.append("Enabled nvidia-persistenced service.")
        
        run_cmd(["nvidia-smi", "-pm", "1"])


        # 3. nvidia-vgpu-vfio module & conf
        run_cmd(["modprobe", "nvidia-vgpu-vfio"])
        conf_path = "/etc/modules-load.d/nvidia-vgpu-vfio.conf"
        try:
            os.makedirs(os.path.dirname(conf_path), exist_ok=True)
            with open(conf_path, "w") as f:
                f.write("nvidia-vgpu-vfio\n")
            fix_results.append(f"Loaded nvidia-vgpu-vfio module and wrote {conf_path}.")
        except Exception as e:
            fix_results.append(f"Failed writing {conf_path}: {e}")

        # 4. vgpu daemons
        run_cmd(["systemctl", "enable", "--now", "nvidia-vgpud", "nvidia-vgpu-mgr"])
        fix_results.append("Enabled and started nvidia-vgpud and nvidia-vgpu-mgr.")

        # 5. nvidia-sriov.service
        service_path = "/etc/systemd/system/nvidia-sriov.service"
        try:
            with open(service_path, "w") as f:
                f.write(SRIOV_SERVICE_CONTENT)
            run_cmd(["systemctl", "daemon-reload"])
            run_cmd(["systemctl", "enable", "--now", "nvidia-sriov.service"])
            fix_results.append(f"Created and enabled {service_path}.")
        except Exception as e:
            fix_results.append(f"Failed configuring {service_path}: {e}")

        recheck = DoctorManager.check_all()
        return {
            "status": "success",
            "message": "Executed automatic fix routines for host virtualization prerequisites.",
            "actions_taken": fix_results,
            "overall_health": recheck["overall_health"],
            "checks": recheck["checks"]
        }
