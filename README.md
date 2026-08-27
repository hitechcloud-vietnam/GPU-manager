# NVIDIA A100 KVM Virtualization System Architecture Specification (ARCHITECTURE.md)

This document defines four core GPU virtualization architectures and a decoupled system structure for `gpu-manager` / `vm-manager` to allocate and manage NVIDIA A100‑PCIE‑40GB GPUs in a Rocky 9.7 KVM environment.

---

## 1. Specification of 4 GPU Virtualization Use Cases

```
                               ┌───────────────────────────────────────────────┐
                               │            NVIDIA A100-PCIE-40GB              │
                               └───────────────────────┬───────────────────────┘
                                                       │
        ┌──────────────────────┬───────────────────────┴───────────────────────┬──────────────────────┐
        ▼                      ▼                                               ▼                      ▼
  [Case 1]               [Case 2]                                        [Case 3]               [Case 4]
  Direct Passthrough     Passthrough + Guest MIG                         Host vGPU              Host MIG + mdev
  ──────────────────     ───────────────────────                         ─────────              ───────────────
  - Full GPU assignment   - Full GPU passthrough, then                      - Host vGPU Manager    - Create MIG on host
  - VFIO-PCI direct bind    enable MIG inside guest                        - mdevctl persistent   - mdevctl persistent
  - ~100% bare-metal perf   - Used for in-guest Kubernetes GPU slicing     - Time-slicing sharing - Full hardware SM/Mem isolation
  - License optional        - License optional                             - vGPU license required - vGPU license required
```

### (1) Case 1: Direct PCIe Passthrough
- Concept: Assign one or more physical GPUs 1:1 to a virtual machine using VFIO-PCI.
- Characteristics: >99% of bare-metal performance, no hardware partitioning, license not required.
- Use: Large LLM model fine-tuning / very large inference workloads.

### (2) Case 2: Direct Passthrough + In‑Guest MIG
- Concept: Passthrough the whole GPU to the VM, then enable MIG inside the guest OS with `nvidia-smi -mig 1`.
- Characteristics: The guest can use Kubernetes `nvidia-device-plugin` to slice GPU resources at Pod granularity.
- Use: Single VM hosting a multi-tenant container cluster.

### (3) Case 3: Host Standard vGPU (vGPU Manager + mdevctl)
- Concept: With MIG disabled on the host, use NVIDIA vGPU Manager to create mediated devices (mdev).
- Characteristics: Time-slicing based division of SM/memory, persistent registration using `mdevctl --auto`.
- Licensing: Requires NVIDIA vGPU (vCS/GRID) licensing.

### (4) Case 4: Host MIG + vGPU mdev (One‑Stop MIG)
- Concept: Create MIG instances (GI/CI) on the host and then create vGPU mdevs on top of those hardware-isolated instances.
- Characteristics: Full hardware isolation for SM/cache/memory plus `mdevctl --auto` persistent registration.
- Licensing: Requires NVIDIA vGPU (vCS/GRID) licensing.

---

## 2. Two Independent Programs (`gpu-manager` vs `vm-manager`)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            /mnt/glue-gfs/tools/                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
       ┌───────────────────────────────┴───────────────────────────────┐
       ▼                                                               ▼
[1. gpu-manager] (GPU Manager)                                    [2. vm-manager] (VM Manager)
─────────────────────────────                                    ───────────────────────────
- Control host physical GPUs & sysfs devices                        - Manage QCOW2 images / storage
- MIG On/Off & one-stop mdev creation                               - Jinja2 XML template rendering (Rocky9, Ubuntu24, Win11)
- Manage mdevctl persistent registration (--auto)                   - Integrate virsh CLI / libvirt SDK control
- Multiple output formatters (text, json, xml)                      - Create/delete VMs and Attach/Detach GPUs (PCIe / mdev)
- Compute remaining capacity per profile                            - Automatic placement of DLS license tokens
```

---

## 3. mdevctl Persistence Mechanism

Integrate with the `mdevctl` system so that vGPU/MIG mdev devices created on a Rocky Linux 9.7 host persist across KVM host reboots.

1. Creation mechanism:
   ```bash
   mdevctl define -u <UUID> -d <PCI_BUS_ID> -t <MDEV_TYPE_ID> --auto
   mdevctl start -u <UUID>
   ```
2. Storage location: `/etc/mdevctl.d/<UUID>` and `/etc/mdevctl.d/auto/<UUID>`
3. Recovery mechanism: mdev devices registered persistently are automatically started at boot when `mdevctl` is enabled via systemd (`systemctl enable mdevctl`).

---

# NVIDIA DLS 3.6.1 (Delegated Licensing System) Official Initial Setup Guide

This guide, based on the official NVIDIA DLS 3.6.1 User Guide (https://docs.nvidia.com/license-system/dls/3.6.1/nvidia-dls-user-guide/index.html), documents the initial deployment of the DLS 3.6.1 appliance, OS administrator permission setup, Portal binding, and guest license integration.

---

## 1. DLS 3.6.1 Specifications and Prerequisites

### ① Minimum System Sizing (VM sizing)
- vCPU: Minimum 4 vCPUs
- RAM: Minimum 8 GB
- Disk: Minimum 15 GB (QCOW2 virtual disk)
- IP configuration: Fixed IP address required (static IP or DHCP reservation)
- Time sync: NTP synchronization required

### ② Network firewall ports (communication)
| Port | Protocol | Direction | Purpose / Service |
| :--: | :------: | :-------: | :---------------- |
| 443  | TLS / TCP | Ingress / Egress | vGPU license lease/renewal, Web UI and REST API access |
| 8081 | TLS / TCP | Ingress | DLS appliance admin web UI initial access |
| 80   | HTTP / TCP | Ingress | Quick release of license when Windows VM shuts down |
| 8082 | TLS / TCP | Bidirectional | HA cluster node synchronization |

### ③ Account structure and sudo enablement (`/etc/adminscripts/enable_sudo.sh`)
- OS-level management accounts (SSH/Console):
  - `dls_admin`: default appliance OS management account (initial password: `welcome`)
  - `rsu_admin`: account for software update/patching (initial password: `welcome`)
- OS sudo enable script:
  - The DLS virtual appliance runs in a hardened environment; to perform management actions (nmcli static IP config, install security scanners, control docker, etc.) run:
    ```bash
    # Enable sudo on the appliance OS console shell
    sudo /etc/adminscripts/enable_sudo.sh
    ```
- Application-level accounts (Web UI / API):
  - DLS 3.6.0+ supports RBAC: `DLS_ADMIN`, `DLS_OPERATOR`, `DLS_USER`
  - On first web UI login, the administrator password for `dls_admin` is created by the user.

---

## 2. [Step 1] Deploy the DLS Appliance VM and configure static IP / sudo

### ① Start KVM VM (UEFI)
```bash
# 1. Clean up previous incomplete VM
virsh destroy nvidia-dls-server 2>/dev/null || true
virsh undefine nvidia-dls-server 2>/dev/null || true

# 2. Start virt-install with bridge-MGMT and UEFI boot
virt-install \
  --name nvidia-dls-server \
  --ram 8192 \
  --vcpus 4 \
  --disk /mnt/glue-gfs/nls-3.6.1-bios.qcow2,bus=virtio \
  --network bridge=bridge-MGMT \
  --boot uefi \
  --os-variant rhel8.0 \
  --import \
  --noautoconsole
```

### ② Set static IP inside the DLS VM OS
Log into the DLS VM console (`virsh console nvidia-dls-server`) as `dls_admin` (password: `welcome`) and run:

```bash
# 1. Enable sudo
sudo /etc/adminscripts/enable_sudo.sh

# 2. List network connections (example interface: eth0 or enp1s0)
nmcli connection show

# 3. Set static IP, gateway, DNS (example: 192.168.1.121/24)
sudo nmcli connection modify enp1s0 \
  ipv4.addresses 192.168.1.121/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns "8.8.8.8 1.1.1.1" \
  ipv4.method manual

# 4. Restart network connection
sudo nmcli connection down enp1s0 && sudo nmcli connection up enp1s0

# 5. (Alternative) Use TUI
sudo nmtui
```

---

## 3. [Step 2] First DLS Web UI Access and Initial Admin Setup

1. Web browser access:
   - Visit `https://<DLS_SERVER_IP>:8081` (example: `https://192.168.1.121:8081`)
   - If certificate warning appears, choose Advanced → Proceed to the IP (unsafe).
2. Create administrator account:
   - Username: `dls_admin`
   - Set and register a custom password.
3. Download DLS Instance Token:
   - After first login, Dashboard → Download DLS Instance Token to retrieve the `dls_instance_token_*.tok` file.

---

## 4. [Step 3] NVIDIA Portal Binding & Download License Server File

> IMPORTANT:
> License matching rule: A `license_*.bin` file must exactly match the DLS Instance Token's unique identifier (JIT UUID). If you reinstall or reset the DLS appliance DB, you must upload the newly generated Instance Token to the Portal and download a new `license_*.bin`. Otherwise you will get `Invalid license file for this service instance`.

1. Login to NVIDIA Licensing Portal: https://nls.nvidia.com
2. Register Service Instance:
   - Menu → Service Instances → Create Service Instance
   - Upload the `dls_instance_token_*.tok` obtained in Step 2.
3. vGPU license entitlement allocation:
   - Select Service Instance → Entitlements → allocate the required number of vGPU / AI Enterprise licenses.
4. Download the License Server File:
   - Actions → Download License Server File to receive the `license_*.bin`.

---

## 5. [Step 4] Upload License to DLS Appliance & Create Client Token

1. Upload `license_*.bin`:
   - In DLS web UI (`https://<DLS_SERVER_IP>:8081`) login → Service Instance Management → Actions → Upload License Server File.
   - Upload the `license_*.bin` from the Portal and verify license counts are activated.
2. Generate Client Configuration Token:
   - In DLS UI → Client Configuration Tokens → Create Client Configuration Token.
   - Download the generated `client_configuration_token_*.tok`.

---

## 6. [Step 5] Apply and Verify Client Token in Guest VMs

### ① Linux Guest VM (RHEL / Rocky / Ubuntu)
```bash
# 1. Place token file in correct directory
sudo mkdir -p /etc/nvidia/ClientConfigToken/
sudo cp client_configuration_token.tok /etc/nvidia/ClientConfigToken/
sudo chmod 744 /etc/nvidia/ClientConfigToken/client_configuration_token.tok

# 2. Restart GRID service
sudo systemctl restart nvidia-gridd

# 3. Verify license acquisition (License Status: Licensed)
nvidia-smi -q | grep -i "License Status"
```

### ② Windows Guest VM
1. Create folder: `C:\Program Files\NVIDIA Corporation\vGPU Licensing\ClientConfigToken\`
2. Copy `client_configuration_token.tok` into that folder.
3. Restart `NVIDIA Display Container LS` service, then run `nvidia-smi -q` and verify `License Status: Licensed`.

---

# GPU‑Manager Complete Reference & Architecture Manual (v2.0)

`gpu-manager` is a high‑performance CLI tool that one‑stop controls Standard vGPU and MIG modes for NVIDIA A100 GPUs in Linux KVM environments, manages mdev device lifecycle, and automates KVM VFIO readiness.

---

## 1. Prerequisites & Automatic Checks (Doctor System)

`gpu-manager` includes `gpu-manager doctor` (or `check`) that diagnoses eight essential host settings and `gpu-manager doctor --fix` which can automatically remediate issues.

### (1) Eight host checks
1. BIOS & kernel IOMMU virtualization: check `intel_iommu=on` / `amd_iommu=on` in `/proc/cmdline`.
2. NVIDIA GRID vGPU Host Driver: check `nvidia-smi` and `/sys/class/mdev_bus/`.
3. `mdevctl` virtualization package: verify installation.
4. NVIDIA Persistence Daemon: check `nvidia-persistenced` service.
5. NVIDIA VFIO kernel module: check `nvidia-vgpu-vfio` is loaded (`modprobe nvidia-vgpu-vfio`).
6. Module auto-load config: check `/etc/modules-load.d/nvidia-vgpu-vfio.conf`.
7. vGPU daemon services: check `nvidia-vgpud` and `nvidia-vgpu-mgr`.
8. SR-IOV boot service: check `/etc/systemd/system/nvidia-sriov.service` exists and is enabled.

### (2) Doctor usage examples
```bash
# Diagnose the 8 host virtualization checks
gpu-manager doctor
# or
gpu-manager check

# Automatically fix missing items (modules, confs, sriov service, etc.)
gpu-manager doctor --fix
# or
gpu-manager check --fix
```

---

## 2. UNIX Standard Reference Header

```text
GPU-MANAGER(8)                 System Administration                 GPU-MANAGER(8)

NAME
       gpu-manager - NVIDIA A100 GPU KVM vGPU & MIG Management Utility

SYNOPSIS
       gpu-manager <COMMAND> [OPTIONS] [ARGUMENTS]

DESCRIPTION
       gpu-manager unified CLI tool manages NVIDIA A100 PCIe GPUs in Linux KVM environments.
       It supports dynamic mode switching (MIG, Standard vGPU, Direct Passthrough), one-stop mdev
       creation with persistent auto-binding (mdevctl --auto), target-based resource destruction,
       and real-time capacity diagnosis.

       It features dynamic shell completion (Tab) for Bash and Zsh shells.
```

---

## 3. Command Quick Reference Matrix

| Command | Alias/Shortcut | Purpose | Key args/flags |
| :--- | :--- | :--- | :--- |
| `set` | N/A | Switch GPU operating mode (MIG, vGPU, Passthrough) | `--gpu <ID>`, `[mig|vgpu|passthrough]`, `--no-reset` |
| `get` | `status` | Output GPU operating mode, hardware status, mdev/MIG occupancy, VFIO readiness | `--gpu <ID|all>`, `--all`, `-o <format>` |
| `create` | `mig create`, `mdev create` | One‑stop hardware MIG + vGPU mdev creation with `mdevctl --auto` persistent register | `--gpu <ID>`, `--profile <NAME>`, `--count <N>`, `--uuid <UUID>` |
| `delete` | N/A | Delete a specific mdev UUID or GPU/all scoped deletion | `--uuid <UUID>`, `--all`, `--gpu <ID>` |
| `reset` | `delete --all` | Full cleanup of resources on a node or a specific GPU | `--gpu <ID>`, `--all` |
| `profiles` | N/A | Show profile memory, slices, max count, and remaining capacity | `--gpu <ID|all>`, `-o <format>` |
| `completion` | N/A | Output Bash/Zsh shell completion scripts | `[bash|zsh]` |
| `mig` | N/A | Legacy MIG control subcommands | `enable`, `disable`, `create`, `destroy` |
| `vgpu` | N/A | Legacy Standard vGPU subcommands | `create`, `delete` |
| `mdev` | N/A | Legacy universal mdev subcommands | `create`, `delete` |

---

## 4. Core Commands Detail & Real-World Examples

### (1) `gpu-manager set` (mode switching)
Cleans up existing resources (mdev and MIG instances) before safely switching modes.

- Syntax: `gpu-manager set --gpu <GPU_ID> [mig | vgpu | passthrough]`
- Examples:
  ```bash
  # Enable MIG mode on GPU 1 (includes GPU reset by default)
  gpu-manager set --gpu 1 mig

  # Switch GPU 1 to Standard vGPU mode (MIG Disabled)
  gpu-manager set --gpu 1 vgpu

  # Prepare GPU 1 for VM Direct Passthrough (VFIO ready)
  gpu-manager set --gpu 1 passthrough
  ```

---

### (2) `gpu-manager get` / `gpu-manager status` (comprehensive status)
Shows current mode, temperature, power, VRAM, slice remaining, active/defined mdev, and VFIO readiness.

- Syntax: `gpu-manager get [--gpu <GPU_ID> | --all]` or `gpu-manager status`
- Examples:
  ```bash
  # Detailed diagnostic for GPU 0
  gpu-manager get --gpu 0

  # Full host diagnostic in pretty JSON
  gpu-manager get --all -o json-pretty
  ```
- Example output:
  ```text
  ========================================================================================
  GPU 1 [00000000:d8:00.0] - NVIDIA A100-PCIE-40GB (MIG Mode: Enabled)
  Temp: 42C | Power: 89W / 250W
  Memory: Used 0MB / Total 40960MB (Free: 40507MB)
  ----------------------------------------------------------------------------------------
    [MIG Instances]
    - Instance ID 1: MIG 3g.20gb (Profile ID 9)
    [mdev Devices]
    - UUID       : b3e78147-b667-41a0-bf3d-70444ae6cb2d
      Profile    : GRID A100-3-20C (Type: nvidia-476)
      sysfs Path : /sys/bus/mdev/devices/b3e78147-b667-41a0-bf3d-70444ae6cb2d
      Persistent : Yes (--auto)
  ========================================================================================
  ```

---

### (3) `gpu-manager create` (one‑stop resource creation)
Parses profile name, creates hardware MIG instances if needed, creates vGPU mdevs on top, and registers them persistently with `mdevctl --auto`.

- Syntax: `gpu-manager create --gpu <GPU_ID> --profile <PROFILE_NAME> [--count N] [--uuid UUID]`
- Examples:
  ```bash
  # Create one GRID A100-3-20C vGPU instance on GPU 1 (persistent auto registration)
  gpu-manager create --gpu 1 --profile "GRID A100-3-20C"

  # Create with a custom UUID
  gpu-manager create --gpu 0 --profile "GRID A100-20C" --uuid "7e123456-7890-4abc-def1-234567890abc"
  ```

---

### (4) `gpu-manager delete` (targeted destroy)
Delete a specific mdev UUID or all resources on a GPU. After removing mdev, also remove orphaned hardware MIG instances.

- Syntax: `gpu-manager delete [--uuid <UUID> | --all --gpu <GPU_ID>]`
- Examples:
  ```bash
  # Delete specific mdev UUID and automatically destroy mapped hardware GI if 1:1
  gpu-manager delete --uuid b3e78147-b667-41a0-bf3d-70444ae6cb2d

  # Delete all mdev and hardware MIG instances on GPU 1
  gpu-manager delete --all --gpu 1
  ```

---

### (5) `gpu-manager reset` (full resource reset)
Destroy all mdev devices and hardware MIG instances on the system or a specified GPU to restore to a clean baseline.

- Syntax: `gpu-manager reset [--gpu <GPU_ID> | --all]`
- Examples:
  ```bash
  # Full reset for GPU 1
  gpu-manager reset --gpu 1

  # Full reset for all GPUs on the host
  gpu-manager reset
  ```

---

### (6) `gpu-manager profiles` (available resources)
Display per-GPU available memory, slice allocation, profile max counts, and remaining capacity.

- Syntax: `gpu-manager profiles [--gpu <GPU_ID|all>]`
- Example:
  ```bash
  gpu-manager profiles --gpu all
  ```

---

## 5. Dynamic Shell Autocomplete Setup Guide (Bash & Zsh)

`gpu-manager` provides dynamic Tab completion for commands, options, available profiles, and existing UUIDs.

### ① Dynamic Autocomplete behaviors
- `gpu-manager create --gpu 1 --profile <TAB>`: only suggest profiles with Remaining Capacity > 0.
- `gpu-manager delete --uuid <TAB>`: suggest only actual defined mdev UUIDs present on the system.

### ② Enable shell completion

- Bash:
  ```bash
  # Apply for current session
  eval "$(gpu-manager completion bash)"

  # Persist system-wide (recommended)
  gpu-manager completion bash > /etc/bash_completion.d/gpu-manager
  ```
- Zsh:
  ```zsh
  # Apply for current session
  eval "$(gpu-manager completion zsh)"
  ```

---

## 6. Cross‑Verification with Native Linux Commands

All resources created and reported by `gpu-manager` can be cross‑checked with native Linux utilities.

```bash
# 1. Check GPU mode and hardware state
nvidia-smi -q -i 0000:d8:00.0 | grep -i "MIG Mode"

# 2. Check hardware MIG instances (GI/CI)
nvidia-smi mig -lgi

# 3. Verify mdevctl persistent registrations and active mdevs
mdevctl list --defined
mdevctl list

# 4. Verify sysfs device nodes for mdev
ls -l /sys/bus/mdev/devices/
```

---

# vm-manager CLI User Guide & XML Template Reference (VM_MANAGER_MANUAL.md)

`vm-manager` is a VM management tool for creating Rocky 9.7, Ubuntu 24.04, and Windows 11 VMs and dynamically attaching/detaching PCI Passthrough devices or vGPU/MIG mdev devices created by `gpu-manager`.

---

## 1. Primary CLI Commands

### (1) Create a VM (`vm-manager create`)
```bash
vm-manager create \
  --name demo-rocky9 \
  --os rocky9 \
  --ram 32G \
  --cpu 8 \
  --disk-size 100G \
  --storage /mnt/glue-gfs/vm-images
```

### (2) Attach GPU to VM (`vm-manager attach-gpu`)
- Direct PCIe Passthrough (Case 1 & Case 2):
  ```bash
  vm-manager attach-gpu --name demo-rocky9 --mode passthrough --pci 0000:3b:00.0
  ```
- vGPU / MIG mdev (Case 3 & Case 4):
  ```bash
  vm-manager attach-gpu --name demo-rocky9 --mode mdev --uuid c9a81b23-8901-41de-9f12-123456789abc
  ```

### (3) Detach GPU (`vm-manager detach-gpu`)
```bash
vm-manager detach-gpu --name demo-rocky9 --uuid c9a81b23-8901-41de-9f12-123456789abc
```

---

## 2. Guest OS XML tuning parameters

| Guest OS | Machine Type | KVM Hidden State | Hugepages / Locked Mem | UEFI / TPM 2.0 |
| :--- | :--- | :--- | :--- | :--- |
| Rocky Linux 9.7 | `pc-q35-rhel9.4.0` | `<kvm><hidden state='on'/></kvm>` | `<memoryBacking><locked/></memoryBacking>` | BIOS or OVMF |
| Ubuntu 24.04 | `pc-q35-9.0` | `<kvm><hidden state='on'/></kvm>` | `<memoryBacking><locked/></memoryBacking>` | BIOS or OVMF |
| Windows 11 | `pc-q35-9.0` | `<kvm><hidden state='on'/></kvm>` | `<memoryBacking><locked/></memoryBacking>` | OVMF UEFI + TPM 2.0 required |

---

# GPU‑Manager Overview (Summary / README)

> Unified CLI toolkit for NVIDIA vGPU & MIG management and host health doctor.

`gpu-manager` manages NVIDIA enterprise GPUs (e.g., A100) in Linux KVM/QEMU hypervisor environments: MIG/vGPU/passthrough mode switching, mdev device lifecycle and persistent registration, host health checks and automatic fixes, and includes a NVIDIA NLS 3.6.1 DLS licensing deployment guide.

---

## Key Features

- Single-command GPU mode switching: `set` (mig, vgpu, passthrough)
- One-stop vGPU lifecycle management: `create` / `delete` / `reset`
  - Create only profiles with remaining capacity > 0
  - Delete by UUID with targeted completion
  - System or per-GPU reset
- Comprehensive status and preflight checks: `get` & `doctor`
- Host virtualization health doctor with auto-fix: `gpu-manager doctor --fix`
- Dynamic shell autocompletion for Bash & Zsh
- Includes NVIDIA NLS 3.6.1 DLS licensing deployment guidance (KVM UEFI deploy, `/etc/adminscripts/enable_sudo.sh`, Portal binding, guest license integration)

---

## Quick Start

### 1. Install
```bash
# Clone repository
git clone https://github.com/<YOUR_GITHUB_ID>/GPU-manager.git
cd GPU-manager

# Build and install (example)
sudo make install
```

### 2. Host checks and automatic fixes
```bash
# Host environment preflight checks
gpu-manager doctor

# Automatically fix missing items
sudo gpu-manager doctor --fix
```

### 3. Enable shell autocomplete
```bash
# Bash
echo 'eval "$(gpu-manager completion bash)"' >> ~/.bashrc
source ~/.bashrc

# Zsh
echo 'eval "$(gpu-manager completion zsh)"' >> ~/.zshrc
source ~/.zshrc
```

---

## Main CLI Commands (Summary)

| Category | Command | Description / Example |
| :--- | :--- | :--- |
| Mode set | `gpu-manager set --gpu <N> <MODE>` | Switch GPU operating mode: `gpu-manager set --gpu 1 vgpu` |
| Status | `gpu-manager get [--gpu <N> | --all]` | `gpu-manager get --gpu 1` |
| Create vGPU | `gpu-manager create --gpu <N> --profile <PROFILE>` | Creates persistent vGPU mdev: `gpu-manager create --gpu 1 --profile "GRID A100-3-20C"` |
| Delete vGPU | `gpu-manager delete --uuid <UUID>` | `gpu-manager delete --uuid 12345678-1234-...` |
| Reset | `gpu-manager reset [--gpu <N>]` | `gpu-manager reset --gpu 1` |
| Doctor | `gpu-manager doctor [--fix]` | `gpu-manager doctor --fix` |

---

License: MIT License (LICENSE)
