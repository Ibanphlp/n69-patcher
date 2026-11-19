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
import math
import os
import shutil
import subprocess
import sys
import tempfile
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
def ssh_forward(port: int = 2222, startup_timeout: float = 8):
    """Start iproxy forwarding local port to device SSH port 22."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=min(0.3, startup_timeout)):
            pass
    except OSError:
        pass
    else:
        say(f"Port {port} already forwarded — reusing existing tunnel", "info")
        yield
        return

    iproxy = get_iproxy()
    proc = subprocess.Popen([str(iproxy), str(port), "22"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + startup_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("iproxy did not open port in time")
            if proc.poll() is not None:
                raise RuntimeError("iproxy exited before opening port")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=min(0.3, remaining)):
                    say(f"iproxy tunnel established on port {port}", "success")
                    break
            except OSError:
                time.sleep(max(0, min(0.2, deadline - time.monotonic())))
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def ssh_exec(port: int, command: str, timeout: float = 10) -> tuple[int, str, str]:
    """Execute command over SSH via iproxy tunnel."""
    sshpass = find_tool("sshpass")
    args = [
        str(sshpass), "-e", "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={math.ceil(timeout)}",
        "-o", "LogLevel=ERROR",
        "-p", str(port), "root@127.0.0.1", command
    ]
    env = os.environ.copy()
    env["SSHPASS"] = "alpine"  # default ramdisk password
    r = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
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

    say("Boot sequence sent. Device boot remains unverified.", "info")

    if capture_logs:
        return capture_kernel_logs(port=args.port, max_wait=args.wait)


SSH_COMMANDS = (
    "sw_vers",
    "mount",
    "cat /cores/*.paniclog 2>/dev/null | head -200",
    "log show --last 5m --style compact 2>/dev/null | tail -200",
    "dmesg 2>/dev/null | tail -100",
)


def format_timeout(error: subprocess.TimeoutExpired) -> str:
    report = f"{error}\n"
    if error.stdout:
        report += f"Partial stdout:\n{error.stdout.decode(errors='replace')}\n"
    if error.stderr:
        report += f"Partial stderr:\n{error.stderr.decode(errors='replace')}\n"
    return report


def collect_ssh_logs(log_file: Path, port: int, wait: int) -> bool:
    """Wait for SSH and record each diagnostic result in the same log."""
    deadline = time.monotonic() + wait
    with log_file.open("w", encoding="utf-8") as log:
        try:
            with ssh_forward(port, startup_timeout=min(8, wait)):
                while (remaining := deadline - time.monotonic()) > 0:
                    try:
                        code, out, err = ssh_exec(port, "echo SSH_OK", timeout=min(5, remaining))
                        if code == 0 and out.strip() == "SSH_OK":
                            break
                        log.write(f"SSH probe exited {code}\n{err}\n")
                    except subprocess.TimeoutExpired as error:
                        log.write(f"SSH probe: {format_timeout(error)}")
                    time.sleep(max(0, min(2, deadline - time.monotonic())))
                else:
                    log.write("Timed out waiting for SSH\n")
                    return False

                for command in SSH_COMMANDS:
                    log.write(f"=== {command} ===\n")
                    try:
                        code, out, err = ssh_exec(port, command)
                        log.write(f"Exit status: {code}\n{out}\n")
                        if err:
                            log.write(f"stderr:\n{err}\n")
                    except subprocess.TimeoutExpired as error:
                        log.write(format_timeout(error))
                    except OSError as error:
                        log.write(f"{error}\n")
                return True
        except (OSError, RuntimeError) as error:
            log.write(f"SSH unavailable: {error}\n")
            return False


def capture_kernel_logs(port: int = 2222, max_wait: int = 180):
    """Capture kernel logs via SSH after bootx."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = Path(tempfile.mkdtemp(prefix="kernel-", dir=LOGS_DIR))
    log_file = out_dir / "ssh.log"
    connected = collect_ssh_logs(log_file, port, max_wait)
    say(f"SSH diagnostics saved to {log_file}", "info" if connected else "warning")
    return 0 if connected else 1


def record_command(out_dir: Path, filename: str, cmd: list[str]) -> str:
    """Save one host diagnostic and keep collecting if the tool fails."""
    try:
        result = run(cmd, capture=True, check=False, timeout=10)
        out = result.stdout.decode(errors="replace")
        err = result.stderr.decode(errors="replace")
        report = out
        if err or result.returncode:
            report += f"\nExit status: {result.returncode}\n{err}"
    except subprocess.TimeoutExpired as error:
        out = ""
        report = format_timeout(error)
    except OSError as error:
        out = ""
        report = f"{error}\n"
    (out_dir / filename).write_text(report, encoding="utf-8")
    return out


def capture_logs(wait: int = 180, port: int = 2222):
    """Collect available host and device diagnostics independently."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = Path(tempfile.mkdtemp(prefix="capture-", dir=LOGS_DIR))

    if sys.platform == "darwin":
        record_command(out_dir, "system-profiler-usb.txt", ["system_profiler", "SPUSBDataType"])
    else:
        record_command(out_dir, "lsusb.txt", ["lsusb"])
    record_command(out_dir, "irecovery.txt", [str(get_irecovery()), "-q"])

    udids = record_command(out_dir, "idevice-id.txt", ["idevice_id", "-l"]).split()
    if udids:
        udid = udids[0]
        record_command(out_dir, "device-info.txt", ["ideviceinfo", "-u", udid])
        record_command(out_dir, "pairing.txt", ["idevicepair", "-u", udid, "validate"])

    if not collect_ssh_logs(out_dir / "ssh.log", port, wait):
        say("SSH unavailable. See ssh.log for details.", "warning")

    if udids:
        record_command(out_dir, "crash-report.txt",
                       ["idevicecrashreport", "-u", udid, "-k", str(out_dir / "crash")])

    say(f"Diagnostics saved to {out_dir}", "info")


def doctor():
    """Verify required host inputs for the Python build and report optional diagnostics."""
    from src.ios16se.build import captured_sep_path, get_ipsw_path, load_config
    from src.ios16se.img4_tool import get_img4_tool

    config = load_config(SCRIPT_DIR / "profiles" / "n69ap_ios16.toml")
    required_ok = True
    say("Checking required host tools...", "info")
    for name in ("irecovery", "gaster", "iBoot64Patcher", "kairos"):
        path = SCRIPT_DIR / config.tools[name]
        if path.is_file() and os.access(path, os.X_OK):
            say(f"  {name}: {path}", "success")
        else:
            required_ok = False
            say(f"  {name}: missing or not executable at {path}", "error")
    try:
        say(f"  img4: {get_img4_tool().binary}", "success")
    except (OSError, RuntimeError) as error:
        required_ok = False
        say(f"  img4: {error}", "error")
    img4tool = find_tool("img4tool")
    resolved_img4tool = str(img4tool) if img4tool.is_absolute() else shutil.which("img4tool")
    if resolved_img4tool:
        say(f"  img4tool: {resolved_img4tool}", "success")
    else:
        required_ok = False
        say("  img4tool: missing or not executable", "error")

    say("Checking firmware and ticket inputs...", "info")
    paths = [get_ipsw_path(config, name) for name in ("target_ios15", "donor_ipados16")]
    paths.extend([Path(config.output["im4m"]), captured_sep_path(config)])
    for relative_path in paths:
        path = SCRIPT_DIR / relative_path
        if path.is_file() and path.stat().st_size > 0:
            say(f"  {path} ({path.stat().st_size:,} bytes)", "success")
        else:
            required_ok = False
            say(f"  Missing or empty: {path}", "error")

    say("Checking optional diagnostics...", "info")
    for name in ("iproxy", "sshpass", "idevice_id", "ideviceinfo", "idevicepair", "idevicecrashreport"):
        path = find_tool(name)
        resolved = str(path) if path.is_absolute() else shutil.which(name)
        if resolved:
            say(f"  {name}: {resolved}", "success")
        else:
            say(f"  {name}: missing", "warning")
    return 0 if required_ok else 1


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
    boot_cap.add_argument("--wait", type=int, default=180, help="Max wait for SSH (seconds)")
    boot_cap.add_argument("--port", type=int, default=2222, help="SSH forward port (default: 2222)")

    # Logs command
    logs_parser = sub.add_parser("logs", help="Capture post-bootx device state")
    logs_parser.add_argument("--wait", type=int, default=180, help="Max wait for SSH (seconds)")
    logs_parser.add_argument("--port", type=int, default=2222, help="SSH forward port (default: 2222)")

    sub.add_parser("status", help="Show build and device status")
    sub.add_parser("doctor", help="Verify host tools and firmware")
    sub.add_parser("clean", help="Remove build artifacts")

    args = parser.parse_args()
    if args.command in ("boot-capture", "logs"):
        if args.wait < 1:
            parser.error("--wait must be at least 1 second")
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")

    if args.command == "build":
        build_images(args.mode, args.im4m)
    elif args.command == "boot":
        boot_device(args)
    elif args.command == "boot-capture":
        return boot_device(args, capture_logs=True)
    elif args.command == "logs":
        capture_logs(wait=args.wait, port=args.port)
    elif args.command == "status":
        status()
    elif args.command == "doctor":
        return doctor()
    elif args.command == "clean":
        clean_all()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nInterrupted by user", "warning")
        sys.exit(130)
    except Exception as e:
        say(f"ERROR: {e}", "error")
        sys.exit(1)