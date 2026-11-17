#!/usr/bin/env python3
"""Desktop control for the N69 iOS 16 tethered install.

Launch from the repo root:

    ./n69ui
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.ios16se.soc import SocMismatch, identity_from_probe  # noqa: E402


def irecovery_path() -> Path | None:
    for candidate in (
        ROOT / "ramdisk-main/Darwin/irecovery",
        ROOT / "tools/SSHRD_Script/Darwin/irecovery",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("irecovery")
    return Path(found) if found else None


def probe_device() -> tuple[int, str]:
    tool = irecovery_path()
    if tool is None:
        return 1, "irecovery was not found in ramdisk-main/Darwin or tools/SSHRD_Script/Darwin."
    try:
        result = subprocess.run([str(tool), "-q"], capture_output=True, text=True)
    except OSError as error:
        return 1, f"irecovery could not run: {error}"
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0 and not text:
        return result.returncode, "No device answered irecovery -q."
    return 0, text or "irecovery returned no identity."


def board_from_probe(text: str) -> str:
    """CPID picks the donor. This TSMC SE prints MODEL n69ap with CPID 0x8003."""

    try:
        return identity_from_probe(text).model
    except SocMismatch:
        return ""


def fields_from_probe(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        found[key.strip().upper()] = value.strip()
    return found


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    detail: str
    group: str
    sudo: bool = False
    confirm: str = ""


ACTIONS: tuple[Action, ...] = (
    Action("detect", "Detect iPhone", "Read irecovery. Does not change the phone.", "Device"),
    Action("ramdisk", "Start ramdisk", "DFU to the SSH ramdisk. SEP stays off unless the switch is on.", "Device"),
    Action("capture", "Capture SEP", "Copy this phone's Preboot SEP and IM4M. Ramdisk, SEP off.", "Device"),
    Action("status", "Volume status", "List APFS roles over the ramdisk.", "Device"),
    Action("volumes", "Create volumes", "Keep SystemB/DataB. Data protection stays none.", "Install"),
    Action("rootfs", "Stream rootfs", "About 25 minutes. Do not sleep the Mac.", "Install", sudo=True),
    Action("cryptex", "Install cryptex", "Dyld cache and Preboot link.", "Install", sudo=True),
    Action("patch", "Patch rootfs", "Shared-Data finish, then mark s8 Recovery.", "Install", sudo=True,
           confirm="This writes the s8 role. iOS 15 System and Data roles stay."),
    Action("list", "List patches", "Show the patch selection. No device writes.", "Install"),
    Action("kernel", "Build display kernel", "Full TSMC or Samsung kernel. No USB decrypt.", "Boot"),
    Action("rebuild", "Full image rebuild", "Decrypts iBSS with gaster. The phone must be in DFU.", "Boot",
           confirm="This talks to DFU and can drop USB during gaster decrypt."),
    Action("send", "Send boot chain", "DFU only. iBSS, iBEC, tree, cache, SEP, kernel, bootx. No go.", "Boot",
           confirm="The phone must be in DFU. Recovery after bootx means the kernel did not start."),
)


def command_for(action: Action, *, mode: str, board: str, sep: bool, dry_run: bool, recreate_data: bool) -> tuple[list[str], dict[str, str]]:
    """Return argv and extra environment for one UI action."""

    n61 = [str(ROOT / "n61.sh"), "--target", "n69-ios16", "-y"]
    if dry_run and action.key not in {"detect", "kernel", "rebuild", "send"}:
        n61.append("--dry-run")
    env: dict[str, str] = {}
    if action.key == "detect":
        tool = irecovery_path()
        if tool is None:
            raise RuntimeError("irecovery was not found.")
        return [str(tool), "-q"], env
    if action.key == "ramdisk":
        env["N61_RAMDISK_SEP"] = "1" if sep else "0"
        return [*n61, "ramdisk"], env
    if action.key == "capture":
        env["N61_RAMDISK_SEP"] = "0"
        return [*n61, "capture-sep"], env
    if action.key == "status":
        return [*n61, "status"], env
    if action.key == "volumes":
        cmd = [*n61, "volumes", "--data-protection", "none"]
        if recreate_data:
            cmd.append("--recreate-data")
        return cmd, env
    if action.key == "rootfs":
        return [*n61, "rootfs"], env
    if action.key == "cryptex":
        return [*n61, "cryptex"], env
    if action.key == "patch":
        return [*n61, "patch-rootfs", "--data-protection", "none", "--features", "core"], env
    if action.key == "list":
        return [*n61, "list"], env
    if action.key == "kernel":
        return [
            sys.executable, str(ROOT / "run_n69_ios16_boot.py"),
            "--im4m", "state/IM4M.bin", "--mode", mode, "--board", board, "--kernel-only",
        ], env
    if action.key == "rebuild":
        return [
            sys.executable, str(ROOT / "run_n69_ios16_boot.py"),
            "--im4m", "state/IM4M.bin", "--mode", mode, "--board", board, "--build-only",
        ], env
    if action.key == "send":
        return [str(ROOT / "send_n69_boot.sh")], env
    raise RuntimeError(f"unknown action {action.key}")


MODE_COPY = {
    "minimal": (
        "Security patches only: AMFI, img4, the root hash, the volume seal, and developer mode. "
        "No SE power chip and no display driver. This mode cannot light the screen."
    ),
    "safe-minimal": (
        "Adds the SE power chip and the SEP request change. Still no display driver. "
        "A lit black screen is as far as a running kernel can get in this mode."
    ),
    "full": (
        "Adds Chestnut, the Pinot display driver, touch, and removal of the iPad display drivers. "
        "This is the only mode that can draw a picture. Sound, Wi-Fi, charging, and vibration stay off."
    ),
}


def run_plan(disk_install: bool) -> list[str]:
    """Steps Start runs, in order. The board comes from the phone, not a switch."""

    steps = ["kernel", "send"]
    if disk_install:
        steps = ["ramdisk", "capture", "volumes", "rootfs", "cryptex", "patch", *steps]
    return steps


def main() -> int:
    try:
        import customtkinter as ctk
    except ImportError:
        print("Install the UI toolkit with: python3 -m pip install customtkinter", file=sys.stderr)
        return 1

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    app = ctk.CTk()
    app.title("N69 iOS 16")
    app.geometry("1180x760")
    app.minsize(980, 640)

    runner: dict[str, subprocess.Popen | None] = {"proc": None}
    lines: queue.Queue[str | None] = queue.Queue()

    app.grid_columnconfigure(1, weight=1)
    app.grid_rowconfigure(0, weight=1)

    side = ctk.CTkScrollableFrame(app, width=420, corner_radius=0)
    side.grid(row=0, column=0, rowspan=3, sticky="nsew")

    ctk.CTkLabel(side, text="N69 iOS 16", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=8, pady=(8, 0))
    ctk.CTkLabel(side, text="The phone is read on its own.", text_color="#9aa4b2").pack(anchor="w", padx=8)

    status = ctk.CTkLabel(side, text="Looking for a phone…", anchor="w", fg_color="#243044", corner_radius=8)
    status.pack(fill="x", padx=8, pady=(14, 8), ipady=8)
    identity = ctk.CTkLabel(side, text="No device yet", anchor="w", justify="left")
    identity.pack(anchor="w", padx=8, pady=(0, 8))

    mode = ctk.StringVar(value="full")
    ctk.CTkLabel(side, text="Kernel mode", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=8, pady=(8, 4))
    for name in ("minimal", "safe-minimal", "full"):
        ctk.CTkRadioButton(side, text=name, variable=mode, value=name).pack(anchor="w", padx=8, pady=(8, 0))
        ctk.CTkLabel(side, text=MODE_COPY[name], wraplength=340, justify="left", text_color="#9aa4b2").pack(anchor="w", padx=28, pady=(2, 4))

    disk_install = ctk.BooleanVar(value=False)
    sep = ctk.BooleanVar(value=False)
    ctk.CTkSwitch(side, text="Also run the disk install", variable=disk_install).pack(anchor="w", padx=8, pady=(12, 2))
    ctk.CTkLabel(
        side,
        text="Off: build the selected kernel and send it. On: ramdisk, SEP capture, volumes, rootfs, cryptex, and the role write first.",
        wraplength=340, justify="left", text_color="#9aa4b2",
    ).pack(anchor="w", padx=8, pady=(0, 6))
    ctk.CTkSwitch(side, text="Load SEP in that ramdisk", variable=sep).pack(anchor="w", padx=8, pady=4)

    ctk.CTkLabel(side, text="Mac password").pack(anchor="w", padx=8, pady=(10, 0))
    password = ctk.CTkEntry(side, show="*", placeholder_text="Used for sudo. Not saved.")
    password.pack(fill="x", padx=8, pady=6)

    log_box = ctk.CTkTextbox(app, font=ctk.CTkFont(family="Menlo", size=12))
    log_box.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=12, pady=12)
    progress = ctk.CTkProgressBar(app)
    progress.grid(row=2, column=1, sticky="ew", padx=12, pady=(0, 12))
    progress.set(0)

    controls = ctk.CTkFrame(side, fg_color="transparent")
    controls.pack(fill="x", padx=8, pady=16)
    start_button = ctk.CTkButton(controls, text="Start")
    start_button.pack(fill="x", pady=(0, 8))
    stop_button = ctk.CTkButton(controls, text="Stop", fg_color="#7f1d1d", hover_color="#991b1b", state="disabled")
    stop_button.pack(fill="x")

    board = {"name": ""}
    cancel = threading.Event()

    def write_log(text: str) -> None:
        log_box.insert("end", text)
        log_box.see("end")

    def set_busy(busy: bool) -> None:
        start_button.configure(state="disabled" if busy else "normal")
        stop_button.configure(state="normal" if busy else "disabled")
        if busy:
            progress.configure(mode="indeterminate")
            progress.start()
        else:
            progress.stop()
            progress.configure(mode="determinate")
            progress.set(0)

    def refresh_probe(text: str) -> None:
        fields = fields_from_probe(text)
        chosen = board_from_probe(text)
        if chosen:
            board["name"] = chosen
        if not fields:
            status.configure(text="No device")
            identity.configure(text="Connect the phone.")
            return
        mode_name = fields.get("MODE", "Unknown")
        pwned = "pwned" if "PWND" in text else "not pwned"
        status.configure(text=f"{mode_name} · {pwned}")
        identity.configure(text=(
            f"{fields.get('NAME', 'iPhone')}  {fields.get('PRODUCT', '')}\n"
            f"CPID {fields.get('CPID', '—')}   {chosen or 'board unknown'}\n"
            f"ECID {fields.get('ECID', '—')}"
        ))

    def stop() -> None:
        cancel.set()
        proc = runner["proc"]
        if proc is not None and proc.poll() is None:
            lines.put("\n[!] Stopping.\n")
            proc.terminate()

    def launch(action: Action, board_name: str) -> int:
        argv, extra = command_for(
            action, mode=mode.get(), board=board_name,
            sep=bool(sep.get()), dry_run=False, recreate_data=False,
        )
        if action.sudo:
            argv = ["sudo", "-S", "-p", "", *argv]
        lines.put(f"\n$ {' '.join(argv)}\n")
        environment = os.environ.copy()
        environment.update(extra)
        proc = subprocess.Popen(
            argv, cwd=ROOT, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        runner["proc"] = proc
        if proc.stdin is not None:
            payload = (password.get() + "\n") if action.sudo else ""
            if action.key == "send":
                payload += "\n"
            if payload:
                proc.stdin.write(payload)
                proc.stdin.flush()
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
            if cancel.is_set():
                proc.terminate()
                break
        return proc.wait()

    def automatic() -> None:
        code, text = probe_device()
        lines.put(text + "\n")
        lines.put("__REFRESH__\n")
        chosen = board_from_probe(text)
        if not chosen:
            lines.put("[x] No N69 is connected. Start stays idle.\n")
            lines.put(None)
            return
        lines.put(f"[*] Board from the phone: {chosen}\n")
        lines.put(f"[*] Kernel mode: {mode.get()}\n{MODE_COPY[mode.get()]}\n")
        if disk_install.get() and not password.get():
            lines.put("[x] The disk install needs the Mac password.\n")
            lines.put(None)
            return
        by_key = {item.key: item for item in ACTIONS}
        for key in run_plan(bool(disk_install.get())):
            if cancel.is_set():
                break
            if key == "send":
                lines.put("\n[*] Put the phone in DFU: hold Power and Home, release Power after 8 seconds, keep Home until the screen stays black.\n")
                while not cancel.is_set():
                    probe_code, probe_text = probe_device()
                    fields = fields_from_probe(probe_text if probe_code == 0 else "")
                    if fields.get("MODE", "").upper() == "DFU":
                        lines.put("[*] DFU seen.\n")
                        break
                    threading.Event().wait(2)
                if cancel.is_set():
                    break
            exit_code = launch(by_key[key], chosen)
            lines.put(f"\n[exit {exit_code}]\n")
            if exit_code != 0:
                lines.put(f"[x] Stopped at {key}.\n")
                break
        lines.put(None)

    def start() -> None:
        if runner["proc"] is not None and runner["proc"].poll() is None:
            return
        cancel.clear()
        set_busy(True)
        threading.Thread(target=automatic, daemon=True).start()

    def watch_device() -> None:
        if runner["proc"] is None or runner["proc"].poll() is not None:
            code, text = probe_device()
            if code == 0 and "CPID" in text:
                refresh_probe(text)
            else:
                status.configure(text="No device")
                identity.configure(text="Connect the phone in DFU, Recovery, or the ramdisk.")
        app.after(2000, watch_device)

    def pump_with_probe() -> None:
        try:
            while True:
                item = lines.get_nowait()
                if item is None:
                    set_busy(False)
                    runner["proc"] = None
                    log_box.insert("end", "\n")
                    log_box.see("end")
                    continue
                if item == "__REFRESH__\n":
                    code, text = probe_device()
                    if code == 0:
                        refresh_probe(text)
                    continue
                log_box.insert("end", item)
                log_box.see("end")
        except queue.Empty:
            pass
        app.after(80, pump_with_probe)

    start_button.configure(command=start)
    stop_button.configure(command=stop)
    app.after(80, pump_with_probe)
    app.after(400, watch_device)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
