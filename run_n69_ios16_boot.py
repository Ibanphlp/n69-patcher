#!/usr/bin/env python3
"""
N69AP iOS 16 Tethered Boot Script
Master orchestrator for iPhone SE 1st Gen (N69AP) iOS 16.7.16 tethered boot

Usage:
    python run_n69_ios16_boot.py [--im4m state/IM4M.bin] [--boot] [--mode full|minimal|safe-minimal]

Boot sequence:
    DFU -> gaster pwn -> iBSS -> iBEC -> DT -> TC -> SEP -> KC -> bootx
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import dataclass

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ios16se.build import (
    BuildConfig, load_config, build_all, boot_flow, validate_boot_images, verify_sep_for_device, BuildError
)


@dataclass
class BootConfig:
    """N69AP iOS 16 boot configuration."""
    im4m_path: Path = Path("state/IM4M.bin")
    boot_mode: str = "full"
    device_board: str = "n69ap"
    root_partition: int = 8
    boot_args: str = (
        "rd=disk0s1s8 amfi_get_out_of_my_way=1 "
        "cs_enforcement_disable=1 wdt=-1 "
        "debug=0x2014e -v keepsyms=1 serial=3"
    )


# ============ PROGRESS UI ============
class ProgressUI:
    """Progress indicators for long-running operations."""

    SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    BAR_FULL = '█'
    BAR_EMPTY = '░'
    CHECK = '✓'
    CROSS = '✗'
    ARROW = '➤'

    COLORS = {
        'RED': '\033[0;31m',
        'GREEN': '\033[0;32m',
        'YELLOW': '\033[1;33m',
        'BLUE': '\033[0;34m',
        'CYAN': '\033[0;36m',
        'MAGENTA': '\033[0;35m',
        'BOLD': '\033[1m',
        'DIM': '\033[2m',
        'NC': '\033[0m',
    }

    def __init__(self):
        self.spinner_idx = 0
        self.spinner_active = False

    def c(self, color):
        return self.COLORS.get(color, '')

    def log(self, msg):
        print(f"{self.c('BLUE')}[INFO]{self.c('NC')} {msg}")

    def ok(self, msg):
        print(f"{self.c('GREEN')}[{self.CHECK}]{self.c('NC')} {msg}")

    def warn(self, msg):
        print(f"{self.c('YELLOW')}[!]{self.c('NC')} {msg}")

    def error(self, msg):
        print(f"{self.c('RED')}[{self.CROSS}]{self.c('NC')} {msg}")

    def step(self, msg):
        print(f"\n{self.c('CYAN')}{self.c('BOLD')}{self.ARROW} {msg}{self.c('NC')}")

    def substep(self, msg):
        print(f"  {self.c('DIM')}{self.ARROW} {msg}{self.c('NC')}")

    def progress_bar(self, current, total, prefix="", suffix=""):
        """Show a progress bar."""
        percent = int(current * 100 / total) if total > 0 else 0
        filled = int(percent * 40 / 100)
        empty = 40 - filled
        bar = self.BAR_FULL * filled + self.BAR_EMPTY * empty
        print(f"\r{self.c('CYAN')}{prefix}{self.c('NC')} [{bar}] {percent:3d}% {suffix}", end='', flush=True)
        if current >= total:
            print()

    def spinner_start(self, msg):
        """Start a spinner animation in background thread."""
        import threading
        import sys
        self.spinner_active = True
        self.spinner_msg = msg
        self.spinner_success = True

        def spin():
            i = 0
            while self.spinner_active:
                ch = self.SPINNER[i % len(self.SPINNER)]
                # Use sys.stderr to avoid interfering with subprocess stdout
                sys.stderr.write(f"\r{self.c('CYAN')}{ch}{self.c('NC')} {msg}")
                sys.stderr.flush()
                time.sleep(0.1)
                i += 1
            if self.spinner_success:
                sys.stderr.write(f"\r{self.c('GREEN')}[{self.CHECK}]{self.c('NC')} {msg}\n")
            else:
                sys.stderr.write(f"\r{self.c('RED')}[{self.CROSS}]{self.c('NC')} {msg} FAILED\n")
            sys.stderr.flush()

        self.spinner_thread = threading.Thread(target=spin, daemon=True)
        self.spinner_thread.start()

    def spinner_stop(self, success=True):
        """Stop the spinner."""
        self.spinner_success = success
        self.spinner_active = False
        if hasattr(self, 'spinner_thread'):
            self.spinner_thread.join(timeout=0.5)


UI = ProgressUI()


def run_with_spinner(cmd, msg, check=True):
    """Run command with spinner animation."""
    UI.spinner_start(msg)
    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        UI.spinner_stop(success=True)
        return result
    except subprocess.CalledProcessError as e:
        UI.spinner_stop(success=False)
        print(f"  stderr: {e.stderr[:500]}")
        raise


def run_with_progress(cmd, msg, total_steps=100, check=True):
    """Run command with progress bar (simulated for unknown duration)."""
    import threading

    UI.spinner_start(msg)

    def progress_updater():
        step = 0
        while UI.spinner_active:
            UI.progress_bar(step, total_steps, msg, f"step {step}/{total_steps}")
            time.sleep(0.5)
            step = min(step + 1, total_steps)

    progress_thread = threading.Thread(target=progress_updater, daemon=True)
    progress_thread.start()

    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        UI.spinner_stop(success=True)
        return result
    except subprocess.CalledProcessError as e:
        UI.spinner_stop(success=False)
        print(f"  stderr: {e.stderr[:500]}")
        raise


def preflight_check(config: BootConfig) -> bool:
    """Verify all prerequisites are met before building."""
    UI.step("Preflight Checks")

    errors = []

    # Check IM4M exists
    UI.substep("Checking IM4M...")
    if not config.im4m_path.exists():
        errors.append(f"IM4M not found: {config.im4m_path}")
        UI.error(f"IM4M: NOT FOUND ({config.im4m_path})")
    else:
        UI.ok(f"IM4M: {config.im4m_path} ({config.im4m_path.stat().st_size:,} bytes)")

    # Check firmware IPSWs exist
    ipsw_dir = Path("firmware")
    ipsw_names = [
        "iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw",
        "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw",
    ]
    UI.substep("Checking IPSWs...")
    for ipsw in ipsw_names:
        ipsw_path = ipsw_dir / ipsw
        if ipsw_path.exists():
            size = ipsw_path.stat().st_size
            UI.ok(f"IPSW: {ipsw} ({size:,} bytes)")
        else:
            errors.append(f"IPSW not found: {ipsw}")
            UI.error(f"IPSW: {ipsw} NOT FOUND")

    # Check tools exist
    tools = [
        "ramdisk-main/Darwin/img4",
        "ramdisk-main/Darwin/gaster",
        "ramdisk-main/Darwin/irecovery",
        "ramdisk-main/Darwin/iBoot64Patcher",
        "tools/dualra1n/binaries/Darwin/kairos",
    ]
    UI.substep("Checking tools...")
    for tool in tools:
        if Path(tool).exists():
            UI.ok(f"Tool: {tool}")
        else:
            errors.append(f"Tool not found: {tool}")
            UI.error(f"Tool: {tool} NOT FOUND")

    # Check existing boot artifacts
    artifacts = [
        "artifacts/boot/boot_iBSS.img4",
        "artifacts/boot/boot_iBEC.img4",
        "artifacts/boot/boot_DT.img4",
        "artifacts/boot/boot_TC.img4",
        "artifacts/boot/boot_KC.img4",
    ]
    UI.substep("Checking boot artifacts...")
    for artifact in artifacts:
        path = Path(artifact)
        if path.exists():
            size = path.stat().st_size
            UI.ok(f"Artifact: {artifact} ({size:,} bytes)")
        else:
            UI.warn(f"Artifact: {artifact} MISSING (will be built)")

    if errors:
        UI.error("Preflight FAILED - The following issues must be resolved:")
        for err in errors:
            print(f"    - {err}")
        return False

    UI.ok("Preflight PASSED")
    return True


def build_boot_images(config: BootConfig) -> bool:
    """Build all boot images using the build module."""
    UI.step("Building Boot Images")
    UI.substep(f"Mode: {config.boot_mode}")

    try:
        profile_path = Path("profiles/n69ap_ios16.toml")
        if not profile_path.exists():
            UI.error(f"Profile not found: {profile_path}")
            return False

        # Load config
        build_config = load_config(profile_path)

        # Override boot mode
        import os
        os.environ["BUILD_MODE"] = config.boot_mode

        # Build with progress tracking
        UI.substep("Starting build pipeline...")

        # We can't easily track individual sub-steps from the build module
        # So we'll use a spinner for the whole build
        UI.spinner_start("Building boot images (iBSS, iBEC, DT, TC, KC, SEP)...")

        try:
            artifacts = build_all(build_config, config.im4m_path)
            UI.spinner_stop(success=True)

            UI.ok("Boot images built successfully:")
            for name, path in [
                ("iBSS", artifacts.ibss),
                ("iBEC", artifacts.ibec),
                ("DT", artifacts.devicetree),
                ("TC", artifacts.trustcache),
                ("KC", artifacts.kernelcache),
                ("SEP", artifacts.sep),
            ]:
                size = path.stat().st_size
                UI.ok(f"  {name}: {path} ({size:,} bytes)")

            return True
        except BuildError as e:
            UI.spinner_stop(success=False)
            UI.error(f"Build failed: {e}")
            return False
        except Exception as e:
            UI.spinner_stop(success=False)
            UI.error(f"Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            return False

    except Exception as e:
        UI.error(f"Build setup failed: {e}")
        return False


def send_boot_chain(config: BootConfig) -> bool:
    """Send boot chain to device in DFU mode with progress."""
    UI.step("Sending Boot Chain to Device")

    irecovery = Path("ramdisk-main/Darwin/irecovery")
    gaster = Path("ramdisk-main/Darwin/gaster")

    boot_chain = [
        (Path("artifacts/boot/boot_iBSS.img4"), "iBSS", None),
        (Path("artifacts/boot/boot_iBEC.img4"), "iBEC", "go"),
        (Path("artifacts/boot/boot_DT.img4"), "DeviceTree", "devicetree"),
        (Path("artifacts/boot/boot_TC.img4"), "TrustCache", "firmware"),
        (Path("artifacts/boot/sep.img4"), "SEP", "rsepfirmware"),
        (Path("artifacts/boot/boot_KC.img4"), "KernelCache", None),
    ]
    try:
        validate_boot_images((image, name) for image, name, _command in boot_chain)
    except BuildError as error:
        UI.error(str(error))
        return False

    if not irecovery.exists():
        UI.error(f"irecovery not found: {irecovery}")
        return False

    # Check device is connected in DFU
    UI.substep("Checking device in DFU mode...")
    result = subprocess.run([str(irecovery), "-q"], capture_output=True, text=True)

    probe_output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "n69ap" not in probe_output.lower():
        UI.error("Device not detected as N69AP in DFU mode")
        UI.error(f"  irecovery output: {probe_output.strip()}")
        return False

    try:
        verify_sep_for_device(load_config(Path("profiles/n69ap_ios16.toml")), config.im4m_path,
                              Path("artifacts/boot/sep.img4"), probe_output)
    except (BuildError, OSError, ValueError) as error:
        UI.error(str(error))
        return False

    if "PWND" not in probe_output:
        UI.substep("Device not pwned, running gaster pwn...")
        run_with_spinner([str(gaster), "pwn"], "Exploiting checkm8 (gaster pwn)")
        run_with_spinner([str(gaster), "reset"], "Resetting device")
        time.sleep(2)

        # Recheck
        result = subprocess.run([str(irecovery), "-q"], capture_output=True, text=True)
        probe_output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 or "n69ap" not in probe_output.lower() or "PWND" not in probe_output:
            UI.error("Device did not return as a pwned N69AP after reset")
            UI.error(f"  irecovery output: {probe_output.strip()}")
            return False

    UI.ok(f"Device detected: {probe_output.strip()}")

    UI.substep(f"Sending {len(boot_chain)} components...")
    for i, (img_path, name, cmd) in enumerate(boot_chain, 1):
        UI.progress_bar(i, len(boot_chain), f"Sending {name}", f"{i}/{len(boot_chain)}")
        subprocess.run([str(irecovery), "-f", str(img_path)], check=True, capture_output=True)
        if cmd == "go":
            # Only A10+ needs `go`; A9 runs iBEC on load.
            q = subprocess.run([str(irecovery), "-q"], capture_output=True, text=True).stdout
            if any(c in q for c in ("0x8010", "0x8011", "0x8012", "0x8015")):
                subprocess.run([str(irecovery), "-c", "go"], check=True, capture_output=True)
            time.sleep(3)
        elif cmd:
            subprocess.run([str(irecovery), "-c", cmd], check=True, capture_output=True)
        time.sleep(1.5)  # Slightly longer delay for reliability

    # Execute bootx
    UI.substep("Executing bootx (kernel starting)...")
    run_with_spinner([str(irecovery), "-c", "bootx"], "Executing bootx")

    UI.ok("Boot sequence sent - check serial console for kernel output")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="N69AP iOS 16 Tethered Boot Script"
    )
    parser.add_argument(
        "--im4m", "-i",
        default="state/IM4M.bin",
        help="Path to IM4M.bin (APTicket)"
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["full", "minimal", "safe-minimal"],
        default="full",
        help="Build mode (default: full)"
    )
    parser.add_argument(
        "--boot", "-b",
        action="store_true",
        help="Send boot chain to device after building"
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Only build, don't boot"
    )

    args = parser.parse_args()

    config = BootConfig(
        im4m_path=Path(args.im4m),
        boot_mode=args.mode,
    )

    # Header
    print(f"\n{UI.c('BOLD')}{UI.c('CYAN')}")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           N69AP iOS 16 Tethered Boot Pipeline                 ║")
    print("║              iPhone SE 1st Gen (A9) → iOS 16.7.16             ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"{UI.c('NC')}")
    print(f"Mode: {UI.c('BOLD')}{config.boot_mode}{UI.c('NC')}")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Preflight
    if not config.im4m_path.exists():
        UI.error(f"IM4M not found: {config.im4m_path}")
        print("    Capture the Preboot SEP and its embedded ticket with ./n61.sh capture-sep --target n69-ios16")
        sys.exit(1)

    if not preflight_check(config):
        sys.exit(1)

    # Build
    if not build_boot_images(config):
        sys.exit(1)

    # Boot if requested
    if args.boot and not args.build_only:
        if not send_boot_chain(config):
            sys.exit(1)

    # Footer
    print(f"\n{UI.c('BOLD')}{UI.c('GREEN')}")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║                         COMPLETE                               ║")
    if args.boot:
        print("║  Chain sent; boot outcome requires device log verification    ║")
    else:
        print("║  Boot images built successfully                                ║")
        print("║  Run with --boot to send to device                            ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"{UI.c('NC')}")
    print(f"Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
