#!/usr/bin/env python3
"""
N69AP iOS 16 Installation Pipeline
Complete end-to-end installation: volumes, rootfs streaming, patches, boot
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from ios16se.build import BuildError, load_config, build_all, validate_boot_images
from ios16se.img4_tool import get_img4_tool


@dataclass
class InstallConfig:
    """Installation configuration."""
    im4m_path: Path = Path("state/IM4M.bin")
    ramdisk_dir: Path = Path("ramdisk-main")
    work_dir: Path = Path("work/install")
    log_file: Path = Path("work/install/install.log")

    # Volumes
    root_partition: int = 8  # s4 = SystemB
    data_partition: int = 5  # s5 = DataB

    # Boot args
    boot_args: str = (
        "rd=disk0s1s8 -v amfi_get_out_of_my_way=1 "
        "cs_enforcement_disable=1 wdt=-1 "
        "debug=0x2014e -v keepsyms=1 serial=3"
    )


class N69InstallError(Exception):
    """Installation error."""
    pass


def run_command(cmd: List[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command, optionally capturing output."""
    if capture:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)
    return subprocess.run(cmd, check=check)


def preflight_checks(config: InstallConfig) -> bool:
    """Verify prerequisites before installation."""
    print("=" * 60)
    print("N69AP iOS 16 Installation - Preflight Checks")
    print("=" * 60)

    errors = []

    # Check IM4M
    if not config.im4m_path.exists():
        errors.append(f"IM4M not found: {config.im4m_path}")
    else:
        print(f"[✓] IM4M: {config.im4m_path}")

    # Check IPSWs
    ipsw_dir = Path("firmware")
    ipsws = [
        "iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw",
        "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw",
    ]
    for ipsw in ipsws:
        if not (ipsw_dir / ipsw).exists():
            errors.append(f"IPSW missing: {ipsw}")
        else:
            print(f"[✓] IPSW: {ipsw}")

    # Check tools
    tools = [
        "ramdisk-main/Darwin/img4",
        "ramdisk-main/Darwin/gaster",
        "ramdisk-main/Darwin/irecovery",
    ]
    for tool in tools:
        if not Path(tool).exists():
            errors.append(f"Tool missing: {tool}")
        else:
            print(f"[✓] Tool: {tool}")

    # Check boot artifacts
    for name in ["boot_iBSS.img4", "boot_iBEC.img4", "boot_DT.img4", "boot_TC.img4", "boot_KC.img4", "sep.img4"]:
        path = Path("artifacts/boot") / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Boot artifact missing: {name}")
        else:
            print(f"[✓] Artifact: {name}")

    if errors:
        print("\n[x] Preflight FAILED:")
        for err in errors:
            print(f"    - {err}")
        return False

    print("\n[✓] Preflight PASSED")
    return True


def create_apfs_volumes(config: InstallConfig, ssh_host: str = None) -> bool:
    """
    Create APFS volumes SystemB (s4) and DataB (s5).

    This requires the SSH ramdisk to be active.
    """
    print("\n" + "=" * 60)
    print("Creating APFS Volumes")
    print("=" * 60)

    # Check if we have ssh access
    # For now, this is a placeholder - full implementation would use ramdisk SSH
    print("[i] APFS volume creation requires ramdisk SSH access")
    print("[i] To create volumes manually:")
    print(f"    ssh root@<device_ip> '/sbin/newfs_apfs -A -v SystemB /dev/disk0s1'")
    print(f"    ssh root@<device_ip> '/sbin/newfs_apfs -A -v DataB -P /dev/disk0s1'")

    # The ramdisk stage.sh has the full implementation
    # See: n61-patcher-master/lib/apfs.sh and stage.sh
    print("[!] Skipping - use n61.sh volumes or run from ramdisk SSH")
    return True


def stream_rootfs(config: InstallConfig, ipSW_path: Path, target_ip: str) -> bool:
    """
    Stream rootfs from IPSW to device via SSH ramdisk.

    This is the ~25 minute operation that installs iOS.
    """
    print("\n" + "=" * 60)
    print("Streaming RootFS")
    print("=" * 60)

    # Extract DMG from IPSW
    print(f"[i] Extracting rootfs from {ipSW_path}...")

    # Find the correct rootfs entry based on IPSW
    if "15.8.8" in str(ipSW_path) or "19H422" in str(ipSW_path):
        # iPhone SE 15.8.8
        dmg_name = "038-87263-058.dmg"
    elif "16.7.16" in str(ipSW_path) or "20H392" in str(ipSW_path):
        # iPad 16.7.16 - this is our donor for clean system
        dmg_name = "038-68261-036.dmg"
    else:
        raise N69InstallError(f"Unknown IPSW: {ipSW_path}")

    # Check if DMG exists in cache or extract it
    cache_path = Path(f"ipsw/cache/{dmg_name}")
    if not cache_path.exists():
        print(f"[i] Extracting {dmg_name} from IPSW...")
        Path("ipsw/cache").mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "unzip", "-o", str(ipSW_path), dmg_name, "-d", "ipsw/cache"
        ], check=True)

    if not cache_path.exists():
        raise N69InstallError(f"DMG not found: {cache_path}")

    print(f"[✓] DMG found: {cache_path} ({cache_path.stat().st_size:,} bytes)")
    print(f"[i] WARNING: Full streaming requires ramdisk SSH access")
    print(f"[i] Use 'sudo ./n61.sh rootfs' for the complete streaming operation")

    return True


def apply_rootfs_patches(config: InstallConfig) -> bool:
    """
    Apply post-rootfs patches:
    - Copy keybags for encrypted DataB
    - Replace fsck/apfs.fs with iOS 14 versions
    - Install SEP firmware
    - Set APFS roles
    """
    print("\n" + "=" * 60)
    print("Applying RootFS Patches")
    print("=" * 60)

    print("[i] The following patches are applied by 'patch-rootfs':")
    print("    - rootfs.finish: Copy var, create xarts/preboot, replace fsck")
    print("    - rootfs.set_roles: Set SystemB=0x1, DataB=0x40")
    print("    - rootfs.health: Health library cache (if enabled)")
    print("    - rootfs.ui_resources: Fix staged App icons")
    print("    - rootfs.trust_cache: Add cdhashes")

    # These are implemented in n61-patcher's lib/overlay.sh
    print("[!] Use 'sudo ./n61.sh patch-rootfs' to apply these patches")

    return True


def build_boot_images(config: InstallConfig) -> bool:
    """Build all boot images (already done, but verify)."""
    print("\n" + "=" * 60)
    print("Verifying Boot Images")
    print("=" * 60)

    artifacts = Path("artifacts/boot")
    artifacts.mkdir(exist_ok=True)

    boot_images = {
        "boot_iBSS.img4": "artifacts/boot/boot_iBSS.img4",
        "boot_iBEC.img4": "artifacts/boot/boot_iBEC.img4",
        "boot_DT.img4": "artifacts/boot/boot_DT.img4",
        "boot_TC.img4": "artifacts/boot/boot_TC.img4",
        "boot_KC.img4": "artifacts/boot/boot_KC.img4",
        "sep.img4": "artifacts/boot/sep.img4",
    }

    all_exist = True
    for name, path in boot_images.items():
        if Path(path).exists():
            size = Path(path).stat().st_size
            print(f"[✓] {name}: {size:,} bytes")
        else:
            print(f"[ ] {name}: missing")
            all_exist = False

    return all_exist


def send_boot_chain(config: InstallConfig) -> bool:
    """Send boot chain to device in DFU mode."""
    print("\n" + "=" * 60)
    print("Sending Boot Chain to Device")
    print("=" * 60)

    irecovery = Path("ramdisk-main/Darwin/irecovery")
    gaster = Path("ramdisk-main/Darwin/gaster")

    if not irecovery.exists():
        print(f"[x] irecovery not found: {irecovery}")
        return False

    # Check DFU mode
    result = subprocess.run([str(irecovery), "-q"], capture_output=True, text=True)
    from .soc import SocMismatch, identity_from_probe
    try:
        identity = identity_from_probe((result.stdout or "") + (result.stderr or ""))
    except SocMismatch as error:
        print(f"[x] Device probe is not an exact n69ap/n69uap match: {error}")
        print(f"    Output: {result.stdout.strip()}")
        return False
    print(f"[i] Device {identity.model} {identity.cpid_text}")

    if "PWND" not in result.stdout:
        print("[i] Device not pwned, running gaster pwn...")
        subprocess.run([str(gaster), "pwn"], check=True)
        subprocess.run([str(gaster), "reset"], check=True)
        time.sleep(3)

    boot_chain = [
        (Path("artifacts/boot/boot_iBSS.img4"), "iBSS", None),
        (Path("artifacts/boot/boot_iBEC.img4"), "iBEC", None),
        (Path("artifacts/boot/boot_DT.img4"), "DeviceTree", "devicetree"),
        (Path("artifacts/boot/boot_TC.img4"), "TrustCache", "firmware"),
        (Path("artifacts/boot/sep.img4"), "SEP", "rsepfirmware"),
        (Path("artifacts/boot/boot_KC.img4"), "KernelCache", None),
    ]
    try:
        validate_boot_images((image, name) for image, name, _command in boot_chain)
    except BuildError as error:
        print(f"[x] {error}")
        return False

    for img_path, name, cmd in boot_chain:
        print(f"[i] Sending {name}...")
        subprocess.run([str(irecovery), "-f", str(img_path)], check=True)
        if cmd:
            subprocess.run([str(irecovery), "-c", cmd], check=True)
        time.sleep(2)

    # bootx
    print("[i] Executing bootx...")
    subprocess.run([str(irecovery), "-c", "bootx"], check=True)

    print("[✓] Boot chain sent - check serial console for kernel output")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="N69AP iOS 16 Installation Pipeline"
    )
    parser.add_argument(
        "--im4m", "-i",
        default="state/IM4M.bin",
        help="Path to IM4M.bin"
    )
    parser.add_argument(
        "--ipsw",
        default="firmware/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw",
        help="Path to SE iOS 15.8.8 IPSW for rootfs streaming"
    )
    parser.add_argument(
        "--boot", "-b",
        action="store_true",
        help="Send boot chain after installation"
    )
    parser.add_argument(
        "--volumes-only", "-v",
        action="store_true",
        help="Only create APFS volumes"
    )
    parser.add_argument(
        "--rootfs-only", "-r",
        action="store_true",
        help="Only stream rootfs"
    )

    parser.parse_args()
    parser.exit(1, "The legacy Python entry point does not install or boot iOS.\n")


if __name__ == "__main__":
    sys.exit(main())
