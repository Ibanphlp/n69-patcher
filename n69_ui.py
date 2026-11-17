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
    result = subprocess.run([str(tool), "-q"], capture_output=True, text=True)
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
    app.grid_rowconfigure(1, weight=1)

    side = ctk.CTkFrame(app, width=300, corner_radius=0)
    side.grid(row=0, column=0, rowspan=2, sticky="nsew")
    side.grid_propagate(False)

    ctk.CTkLabel(side, text="N69 iOS 16", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=18, pady=(18, 0))
    ctk.CTkLabel(side, text="iPhone SE · tethered 16.7.16", text_color="#9aa4b2").pack(anchor="w", padx=18)

    status = ctk.CTkLabel(side, text="No device yet", anchor="w", fg_color="#243044", corner_radius=8)
    status.pack(fill="x", padx=16, pady=(16, 8), ipady=8)

    info = ctk.CTkTextbox(side, height=210, font=ctk.CTkFont(family="Menlo", size=12))
    info.pack(fill="x", padx=16, pady=4)
    info.insert("end", "Press Detect.\nDFU, Recovery, and the ramdisk all answer.")
    info.configure(state="disabled")

    mode = ctk.StringVar(value="full")
    ctk.CTkLabel(side, text="Kernel").pack(anchor="w", padx=18, pady=(12, 0))
    ctk.CTkSegmentedButton(side, values=["minimal", "safe-minimal", "full"], variable=mode).pack(fill="x", padx=16, pady=6)

    board = ctk.StringVar(value="n69uap")
    ctk.CTkLabel(side, text="Board").pack(anchor="w", padx=18)
    ctk.CTkSegmentedButton(side, values=["n69uap", "n69ap"], variable=board).pack(fill="x", padx=16, pady=6)

    sep = ctk.BooleanVar(value=False)
    dry = ctk.BooleanVar(value=False)
    recreate = ctk.BooleanVar(value=False)
    ctk.CTkSwitch(side, text="Ramdisk SEP via iBoot", variable=sep).pack(anchor="w", padx=18, pady=4)
    ctk.CTkSwitch(side, text="Dry run", variable=dry).pack(anchor="w", padx=18, pady=4)
    ctk.CTkSwitch(side, text="Recreate DataB", variable=recreate).pack(anchor="w", padx=18, pady=4)

    ctk.CTkLabel(side, text="Mac password for sudo steps").pack(anchor="w", padx=18, pady=(10, 0))
    password = ctk.CTkEntry(side, show="*", placeholder_text="Not saved")
    password.pack(fill="x", padx=16, pady=6)

    main = ctk.CTkFrame(app, fg_color="transparent")
    main.grid(row=0, column=1, sticky="nsew", padx=12, pady=(12, 0))
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(1, weight=1)

    ctk.CTkLabel(main, text="Actions", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
    buttons = ctk.CTkFrame(main, fg_color="transparent")
    buttons.grid(row=1, column=0, sticky="new", pady=8)
    buttons.grid_columnconfigure(0, weight=1)

    log_frame = ctk.CTkFrame(app, fg_color="transparent")
    log_frame.grid(row=1, column=1, sticky="nsew", padx=12, pady=(0, 4))
    log_frame.grid_rowconfigure(1, weight=1)
    log_frame.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(log_frame, text="Log", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
    log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Menlo", size=12))
    log_box.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
    progress = ctk.CTkProgressBar(app)
    progress.grid(row=2, column=1, sticky="ew", padx=12, pady=(0, 8))
    progress.set(0)

    controls = ctk.CTkFrame(side, fg_color="transparent")
    controls.pack(side="bottom", fill="x", padx=16, pady=16)
    stop_button = ctk.CTkButton(controls, text="Stop", fg_color="#7f1d1d", hover_color="#991b1b", state="disabled")
    stop_button.pack(fill="x")

    widgets: list[ctk.CTkButton] = []

    def write_log(text: str) -> None:
        log_box.insert("end", text)
        log_box.see("end")

    def set_info(text: str) -> None:
        info.configure(state="normal")
        info.delete("1.0", "end")
        info.insert("end", text)
        info.configure(state="disabled")

    def set_busy(busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in widgets:
            widget.configure(state=state)
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
            board.set(chosen)
        if not fields:
            status.configure(text="No device")
            set_info(text)
            return
        mode_name = fields.get("MODE", "Unknown")
        pwned = "PWND" if "PWND" in text else "not pwned"
        status.configure(text=f"{mode_name} · {pwned}")
        rows = [
            f"Name     {fields.get('NAME', '—')}",
            f"Product  {fields.get('PRODUCT', '—')}",
            f"Model    {fields.get('MODEL', '—')}",
            f"CPID     {fields.get('CPID', '—')}",
            f"ECID     {fields.get('ECID', '—')}",
            f"Board    {chosen or 'unknown'}",
            f"SCEP     {fields.get('SCEP', '—')}",
        ]
        set_info("\n".join(rows))

    def stop() -> None:
        proc = runner["proc"]
        if proc is None or proc.poll() is not None:
            return
        write_log("\n[!] Stopping.\n")
        proc.terminate()

    def run_action(action: Action) -> None:
        if runner["proc"] is not None and runner["proc"].poll() is None:
            return
        if action.confirm:
            dialog = ctk.CTkInputDialog(text=action.confirm + "\n\nType yes to continue.", title=action.label)
            if (dialog.get_input() or "").strip().lower() != "yes":
                write_log(f"[!] {action.label} cancelled.\n")
                return
        if action.sudo and not password.get():
            write_log("[x] Enter the Mac password. It is not stored.\n")
            return
        try:
            argv, extra = command_for(
                action,
                mode=mode.get(),
                board=board.get(),
                sep=bool(sep.get()),
                dry_run=bool(dry.get()),
                recreate_data=bool(recreate.get()),
            )
        except RuntimeError as error:
            write_log(f"[x] {error}\n")
            return
        if action.sudo:
            argv = ["sudo", "-S", "-p", "", *argv]
        write_log(f"\n$ {' '.join(argv)}\n")
        environment = os.environ.copy()
        environment.update(extra)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            write_log(f"[x] {error}\n")
            return
        if proc.stdin is not None:
            payload = ""
            if action.sudo:
                payload += password.get() + "\n"
            # send_n69_boot.sh waits for Enter. The confirm dialog already
            # asked, and a GUI pipe has no keyboard for that prompt.
            if action.key == "send":
                payload += "\n"
            if payload:
                proc.stdin.write(payload)
                proc.stdin.flush()
        runner["proc"] = proc
        set_busy(True)

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            code = proc.wait()
            lines.put(f"\n[exit {code}]\n")
            if action.key == "detect":
                lines.put("__PROBE__\n")
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()

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
                if item == "__PROBE__\n":
                    code, text = probe_device()
                    if code == 0:
                        refresh_probe(text)
                    continue
                log_box.insert("end", item)
                log_box.see("end")
        except queue.Empty:
            pass
        app.after(80, pump_with_probe)

    stop_button.configure(command=stop)
    last_group = ""
    for action in ACTIONS:
        if action.group != last_group:
            ctk.CTkLabel(buttons, text=action.group, font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(10, 2))
            last_group = action.group
        row = ctk.CTkFrame(buttons, fg_color="transparent")
        row.pack(fill="x", pady=3)
        button = ctk.CTkButton(row, text=action.label, width=180, command=lambda item=action: run_action(item))
        button.pack(side="left")
        widgets.append(button)
        ctk.CTkLabel(row, text=action.detail, anchor="w", text_color="#9aa4b2").pack(side="left", padx=10)

    app.after(80, pump_with_probe)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
