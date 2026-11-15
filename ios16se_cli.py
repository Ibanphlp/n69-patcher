#!/usr/bin/env python3
"""
iOS 16 SE Tethered Boot CLI — unified pipeline with serial capture.

Commands:
  build       Build all boot images (iBSS, iBEC, DT, TC, SEP, KC)
  boot        Send images to device in DFU mode (gaster pwn → iBSS → iBEC → DT → TC → SEP → KC → bootx)
  boot-capture  Boot + capture kernel logs via SSH after bootx
  logs        Capture device state after bootx (USB, irecovery, pairing, SSH, panic reports)
  status      Show build status and device info
  doctor      Verify host tools and firmware
  clean       Remove build artifacts
"""

import argparse
import json
import os
import subprocess
import sys
import time
import socket
import threading
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SCRIPT_DIR / "artifacts" / "boot"
STATE_DIR = SCRIPT_DIR / "state"
LOGS_DIR = SCRIPT_DIR / "logs"


def say(msg: str, level: str = "info"):
    colors = {"info": "\033[1;34m", "success": "\033[1;32m", "warning": "\033[1;33m", "error": "\033[1;31m"}
    reset = "\033[0m"
    prefix = {"info": "==>", "success": "✓", "warning": "!!", "error": "✗"}[level]
    print(f"{colors[level]}{prefix} {msg}{reset}", flush=True)


def run(cmd, check=True, capture=False, timeout=120, live=False, env=None):
    """Run command with optional live output."""
    e = os.environ.copy()
    if env:
        e.update(env)
    if live:
        # Use PTY for reliable USB/DFU interaction (like n56 and SevenUp)
        import pty
        master, slave = pty.openpty()
        proc = subprocess.Popen(cmd, stdout=slave, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, env=e, close_fds=True)
        os.close(slave)
        out = b""
        try:
            while True:
                data = os.read(master, 1024)
                if not data:
                    break
                out += data
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except OSError:
            pass
        finally:
            os.close(master)
        return proc.wait()
    if capture:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=e)
        if check and r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
        return r
    else:
        r = subprocess.run(cmd, check=check, timeout=timeout, env=e)
        return r


def find_tool(name: str) -> Path:
    """Locate binary tool."""
    candidates = [
        SCRIPT_DIR / "ramdisk-main" / "Darwin" / name,
        SCRIPT_DIR / "ramdisk-main" / "Linux" / name,
        SCRIPT_DIR / "tools" / name / "bin" / name,
        SCRIPT_DIR / "tools" / name / name,
        Path(name),
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_mode & 0o111:
            return c.resolve()
    return Path(name)


def get_irecovery() -> Path:
    return find_tool("irecovery")


def get_gaster() -> Path:
    return find_tool("gaster")


def get_iproxy() -> Path:
    return find_tool("iproxy")


def check_device() -> bool:
    """Verify device is N69AP in DFU mode."""
    try:
        r = run([str(get_irecovery()), "-q"], capture=True, check=False)
        out = r.stdout.decode(errors="replace")
        if "n69ap" not in out.lower():
            say(f"Device is not N69AP in DFU mode:\n{out}", "error")
            return False
        return True
    except Exception as e:
        say(f"Error checking device: {e}", "error")
        return False


def is_pwnd() -> bool:
    """Check if device is already in PWND DFU mode."""
    try:
        r = run([str(get_irecovery()), "-q"], capture=True, check=False)
        return "PWND" in r.stdout.decode(errors="replace")
    except Exception:
        return False


def pwn_device():
    """Pwn device with gaster if not already pwnd."""
    if is_pwnd():
        say("Device already in PWND DFU mode — skipping gaster pwn", "info")
        return
    say("Pwning device with gaster...", "info")
    env = os.environ.copy()
    env["USB_TIMEOUT"] = "5000"
    try:
        run([str(get_gaster()), "pwn"], env=env, check=True, timeout=90)
        time.sleep(1)
        run([str(get_gaster()), "reset"], check=False, capture=True)
        time.sleep(2)
        say("Device pwned successfully", "success")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        say(f"gaster pwn failed/timed out: {e}", "error")
        if is_pwnd():
            say("Device is in PWND state despite error — proceeding", "warning")
        else:
            say("ERROR: Device failed to enter PWND DFU mode. Re-plug USB and re-enter DFU.", "error")
            sys.exit(1)


def send_image(img_path: Path, name: str):
    """Send IMG4 file via irecovery with retries."""
    say(f"Sending {name} ({img_path.name})...", "info")
    for attempt in range(10):
        try:
            run([str(get_irecovery()), "-f", str(img_path)], check=True)
            break
        except subprocess.CalledProcessError:
            if attempt == 9:
                raise
            say(f"  Attempt {attempt+1} failed, retrying in 3s...", "warning")
            time.sleep(3)


def execute_command(cmd: str):
    """Execute irecovery command."""
    run([str(get_irecovery()), "-c", cmd], check=True)


@contextmanager
def ssh_forward(port: int = 2222):
    """Start iproxy forwarding local port to device SSH port 22."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            say(f"Port {port} already forwarded — reusing existing tunnel", "info")
            yield
            return
    except OSError:
        pass

    iproxy = get_iproxy()
    proc = subprocess.Popen([str(iproxy), str(port), "22"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("iproxy exited before opening port")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    say(f"iproxy tunnel established on port {port}", "success")
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("iproxy did not open port in time")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def ssh_exec(port: int, command: str, timeout: int = 10) -> tuple[int, str, str]:
    """Execute command over SSH via iproxy tunnel."""
    sshpass = find_tool("sshpass")
    args = [
        str(sshpass), "-e", "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "LogLevel=ERROR",
        "-p", str(port), "root@127.0.0.1", command
    ]
    env = os.environ.copy()
    env["SSHPASS"] = "alpine"  # default ramdisk password
    r = subprocess.run(args, capture_output=True, timeout=timeout+5, env=env)
    return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")


def build_images(mode: str = "full", im4m: Optional[Path] = None):
    """Build all boot images."""
    if im4m is None:
        im4m = STATE_DIR / "IM4M.bin"
    if not im4m.exists():
        say(f"IM4M not found: {im4m}. Extract it first.", "error")
        sys.exit(1)

    say(f"Building boot images (mode: {mode})...", "info")
    os.environ["BUILD_MODE"] = mode
    try:
        run([
            sys.executable, "-m", "src.ios16se.build",
            str(SCRIPT_DIR / "profiles" / "n69ap_ios16.toml"),
            "--im4m", str(im4m)
        ], check=True, live=True)
        say("Build complete", "success")
    except subprocess.CalledProcessError:
        say("Build failed", "error")
        sys.exit(1)


def boot_device(args, capture_logs: bool = False):
    """Execute full boot sequence."""
    build_images(args.mode, args.im4m)

    if not check_device():
        sys.exit(1)

    images = {
        "iBSS": IMAGES_DIR / "boot_iBSS.img4",
        "iBEC": IMAGES_DIR / "boot_iBEC.img4",
        "DeviceTree": IMAGES_DIR / "boot_DT.img4",
        "TrustCache": IMAGES_DIR / "boot_TC.img4",
        "SEP": IMAGES_DIR / "sep.img4",
        "KernelCache": IMAGES_DIR / "boot_KC.img4",
    }

    # Verify all images exist
    for name, path in images.items():
        if not path.exists():
            say(f"Missing {name}: {path}", "error")
            sys.exit(1)

    pwn_device()

    say("=== Sending boot images ===", "info")

    # 1. iBSS
    send_image(images["iBSS"], "iBSS")
    time.sleep(2)

    # 2. iBEC + go
    send_image(images["iBEC"], "iBEC")
    execute_command("go")
    time.sleep(2)

    # 3. DeviceTree
    send_image(images["DeviceTree"], "DeviceTree")
    execute_command("devicetree")

    # 4. TrustCache
    send_image(images["TrustCache"], "TrustCache")
    execute_command("firmware")

    # 5. SEP
    send_image(images["SEP"], "SEP")
    execute_command("rsepfirmware")

    # 6. KernelCache
    send_image(images["KernelCache"], "KernelCache")

    # Final bootx
    say("Executing bootx...", "info")
    execute_command("bootx")

    say("Boot sequence sent. Device should now be booting iOS 16.", "success")

    if capture_logs:
        say("Waiting for SSH to become available...", "info")
        time.sleep(10)
        capture_kernel_logs()


def capture_kernel_logs(port: int = 2222, max_wait: int = 180):
    """Capture kernel logs via SSH after bootx (SevenUp/n56 style)."""
    say("Starting kernel log capture via SSH...", "info")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"kernel_{time.strftime('%Y%m%d-%H%M%S')}.log"

    deadline = time.monotonic() + max_wait
    ssh_ready = False
    passwords = ["alpine", "root", ""]

    with ssh_forward(port):
        while time.monotonic() < deadline:
            if not ssh_ready:
                for pw in passwords:
                    env = os.environ.copy()
                    env["SSHPASS"] = pw
                    r = subprocess.run([
                        str(find_tool("sshpass")), "-e", "ssh",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "ConnectTimeout=5",
                        "-o", "LogLevel=ERROR",
                        "-p", str(port), "root@127.0.0.1", "echo SSH_OK"
                    ], capture_output=True, timeout=10, env=env)
                    if b"SSH_OK" in r.stdout:
                        ssh_ready = True
                        say(f"SSH connected (password: '{pw}')", "success")
                        break
                if not ssh_ready:
                    say("  Waiting for SSH...", "info")
                    time.sleep(5)
                    continue

            # SSH ready — capture logs
            say("Capturing kernel logs...", "info")
            with open(log_file, "a") as f:
                # System version
                code, out, _ = ssh_exec(port, "sw_vers")
                if code == 0:
                    f.write(f"=== sw_vers ===\n{out}\n")
                    print(out.strip())

                # Mounts
                code, out, _ = ssh_exec(port, "mount")
                if code == 0:
                    f.write(f"=== mount ===\n{out}\n")

                # Panic logs
                code, out, _ = ssh_exec(port, "cat /cores/*.paniclog 2>/dev/null | head -200")
                if code == 0 and out.strip():
                    f.write(f"=== PANIC LOG ===\n{out}\n")
                    say("!!! PANIC LOG CAPTURED !!!", "warning")
                    print(out)
                else:
                    f.write("=== No panic log yet ===\n")

                # Recent kernel log
                code, out, _ = ssh_exec(port, "log show --last 5m --style compact 2>/dev/null | tail -200")
                if code == 0:
                    f.write(f"=== log show (last 5m) ===\n{out}\n")
                    print(out)

                # dmesg equivalent
                code, out, _ = ssh_exec(port, "dmesg 2>/dev/null | tail -100")
                if code == 0 and out.strip():
                    f.write(f"=== dmesg ===\n{out}\n")

            say(f"Logs saved to {log_file}", "success")
            return 0

    say("Timed out waiting for SSH", "error")
    return 1


def capture_logs(wait: int = 180):
    """Full post-bootx diagnostic capture (our existing capture_logs.sh in Python)."""
    say(f"Capturing device state for up to {wait}s...", "info")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = LOGS_DIR / f"capture-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)

    # 1. USB enumeration
    say("1/5 USB enumeration", "info")
    r = run(["lsusb"], capture=True, check=False)
    (out_dir / "lsusb.txt").write_text(r.stdout.decode(errors="replace"))

    # 2. irecovery mode
    say("2/5 irecovery mode", "info")
    r = run([str(get_irecovery()), "-q"], capture=True, check=False)
    irec_out = r.stdout.decode(errors="replace")
    (out_dir / "irecovery.txt").write_text(irec_out)
    print(irec_out)

    # 3. Pairing
    say("3/5 libimobiledevice pairing", "info")
    r = run(["idevice_id", "-l"], capture=True, check=False)
    udids = r.stdout.decode().strip().split()
    if udids:
        udid = udids[0]
        say(f"UDID: {udid}", "info")
        r = run(["ideviceinfo", "-u", udid], capture=True, check=False)
        (out_dir / "device-info.txt").write_text(r.stdout.decode(errors="replace"))
        r = run(["idevicepair", "-u", udid, "pair"], capture=True, check=False)
        if r.returncode == 0:
            say("Pairing successful", "success")
        else:
            say("Pairing failed — accept 'Trust' on device if screen is on", "warning")

    # 4. SSH + logs
    say("4/5 SSH via iproxy", "info")
    with ssh_forward():
        time.sleep(2)
        for pw in ["alpine", "root"]:
            code, out, _ = ssh_exec(2222, "echo SSH_OK")
            if code == 0 and "SSH_OK" in out:
                say(f"SSH works (password: {pw})", "success")
                for cmd in ["sw_vers", "mount", "cat /cores/*.paniclog 2>/dev/null | head -200"]:
                    code, out, _ = ssh_exec(2222, cmd)
                    (out_dir / "ssh.log").write_text(out)
                    print(out)
                break
        else:
            say("SSH unavailable (no ramdisk sshd or rootfs not mounted)", "warning")

    # 5. Crash reports
    say("5/5 Crash reports", "info")
    if udids:
        r = run(["idevicecrashreport", "-u", udid, "-k", str(out_dir / "crash")], capture=True, check=False)
        if r.returncode == 0:
            panic_files = list((out_dir / "crash").glob("*.panic"))
            if panic_files:
                say("PANIC REPORT FOUND:", "warning")
                print(panic_files[0].read_text()[:2000])
            else:
                say("No .panic file yet — kernel panics surface ~1-2 min after boot", "info")

    say(f"Artifacts saved to: {out_dir}", "success")


def doctor():
    """Verify host tools and firmware."""
    say("Checking host tools...", "info")
    tools = ["irecovery", "gaster", "img4", "img4tool", "iBoot64Patcher", "kairos", "iproxy", "sshpass", "idevice_id", "ideviceinfo", "idevicepair", "idevicecrashreport"]
    for t in tools:
        p = find_tool(t)
        if p.exists() or t in ["idevice_id", "ideviceinfo", "idevicepair", "idevicecrashreport"]:
            say(f"  {t}: {p if p.exists() else 'system PATH'}", "success")
        else:
            say(f"  {t}: MISSING", "error")

    # Check IPSWs
    say("\nChecking firmware...", "info")
    ipsws = [
        ("iPhone SE iOS 15.8.8", SCRIPT_DIR / "firmware" / "iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"),
        ("iPad 5 iPadOS 16.7.16", SCRIPT_DIR / "firmware" / "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"),
    ]
    for name, path in ipsws:
        if path.exists():
            say(f"  {name}: {path} ({path.stat().st_size:,} bytes)", "success")
        else:
            say(f"  {name}: MISSING at {path}", "error")

    # Check IM4M
    im4m = STATE_DIR / "IM4M.bin"
    if im4m.exists():
        say(f"  IM4M: {im4m} ({im4m.stat().st_size:,} bytes)", "success")
    else:
        say(f"  IM4M: MISSING at {im4m}", "error")


def clean_all():
    """Remove build artifacts."""
    say("Cleaning build artifacts...", "info")
    dirs = [
        SCRIPT_DIR / "work",
        SCRIPT_DIR / "artifacts" / "boot",
        SCRIPT_DIR / "logs",
    ]
    for d in dirs:
        if d.exists():
            import shutil
            shutil.rmtree(d)
            say(f"  Removed {d}", "info")
    say("Clean complete", "success")


def status():
    """Show build status."""
    say("Build status:", "info")
    for name in ["boot_iBSS.img4", "boot_iBEC.img4", "boot_DT.img4", "boot_TC.img4", "sep.img4", "boot_KC.img4", "boot.json"]:
        p = IMAGES_DIR / name
        if p.exists():
            say(f"  {name}: {p.stat().st_size:,} bytes", "success")
        else:
            say(f"  {name}: MISSING", "warning")

    say("\nIM4M:", "info")
    im4m = STATE_DIR / "IM4M.bin"
    if im4m.exists():
        say(f"  {im4m}: {im4m.stat().st_size:,} bytes", "success")
    else:
        say("  MISSING", "error")

    # Device status
    say("\nDevice status:", "info")
    try:
        r = run([str(get_irecovery()), "-q"], capture=True, check=False)
        out = r.stdout.decode(errors="replace")
        for line in out.strip().split("\n"):
            print(f"  {line}")
    except Exception as e:
        say(f"  Could not query device: {e}", "error")


def main():
    parser = argparse.ArgumentParser(
        prog="ios16se",
        description="iOS 16 SE Tethered Boot Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ios16se build --mode full
  ios16se boot --mode safe-minimal
  ios16se boot-capture --mode full
  ios16se logs --wait 180
  ios16se doctor
  ios16se status
        """
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Build command
    build_parser = sub.add_parser("build", help="Build all boot images")
    build_parser.add_argument("--mode", choices=["full", "minimal", "safe-minimal"], default="full",
                              help="Build mode (default: full)")
    build_parser.add_argument("--im4m", type=Path, help="Path to IM4M.bin (default: state/IM4M.bin)")

    # Boot command
    boot_parser = sub.add_parser("boot", help="Boot device (send images + bootx)")
    boot_parser.add_argument("--mode", choices=["full", "minimal", "safe-minimal"], default="full",
                             help="Build mode (default: full)")
    boot_parser.add_argument("--im4m", type=Path, help="Path to IM4M.bin (default: state/IM4M.bin)")

    # Boot-capture command
    boot_cap = sub.add_parser("boot-capture", help="Boot device and capture kernel logs via SSH")
    boot_cap.add_argument("--mode", choices=["full", "minimal", "safe-minimal"], default="full",
                          help="Build mode (default: full)")
    boot_cap.add_argument("--im4m", type=Path, help="Path to IM4M.bin (default: state/IM4M.bin)")
    boot_cap.add_argument("--wait", type=int, default=180, help="Max wait for SSH")

    # Logs command
    logs_parser = sub.add_parser("logs", help="Capture post-bootx device state")
    logs_parser.add_argument("--wait", type=int, default=180, help="Max wait time for SSH/logs (seconds)")
    logs_parser.add_argument("--port", type=int, default=2222, help="SSH forward port (default: 2222)")

    sub.add_parser("status", help="Show build and device status")
    sub.add_parser("doctor", help="Verify host tools and firmware")
    sub.add_parser("clean", help="Remove build artifacts")

    args = parser.parse_args()

    if args.command == "build":
        build_images(args.mode, args.im4m)
    elif args.command == "boot":
        boot_device(args)
    elif args.command == "boot-capture":
        boot_device(args, capture_logs=True)
    elif args.command == "logs":
        capture_logs(args.wait)
    elif args.command == "status":
        status()
    elif args.command == "doctor":
        doctor()
    elif args.command == "clean":
        clean_all()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nInterrupted by user", "warning")
        sys.exit(130)
    except Exception as e:
        say(f"ERROR: {e}", "error")
        sys.exit(1)