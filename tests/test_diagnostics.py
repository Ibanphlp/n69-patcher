import contextlib
from datetime import datetime, timedelta
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ios16se_cli as cli
from src.ios16se.build import load_config
from src.ios16se.firmware import verify


class DiagnosticsTests(unittest.TestCase):
    def test_doctor_finds_bundled_img4_outside_repository(self):
        root = Path(cli.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as working_dir:
            # Use a fixture binary inside a separate project directory and
            # disable the PATH fallback so the result is independent of the host.
            project = Path(working_dir) / "project"
            tool = project / "ramdisk-main" / "Linux" / "img4"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"\x7fELF")
            tool.chmod(0o755)
            output = io.StringIO()
            config = load_config(root / "profiles" / "n69ap_ios16.toml")
            with (
                patch.object(cli, "SCRIPT_DIR", project),
                patch.object(cli.sys, "platform", "linux"),
                patch("src.ios16se.build.load_config", return_value=config),
                patch("shutil.which", return_value=None),
                contextlib.redirect_stdout(output),
            ):
                cli.doctor()
            self.assertIn(f"img4: {tool}", output.getvalue())

    def test_doctor_requires_nonempty_inputs_and_executable_tools(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            root = Path(root)
            config = SimpleNamespace(
                tools={name: name for name in ("irecovery", "gaster", "iBoot64Patcher", "kairos")},
                output={"im4m": "state/IM4M.bin"},
            )
            inputs = (Path("target.ipsw"), Path("donor.ipsw"),
                      Path("state/IM4M.bin"), Path("state/sep-firmware.img4"))
            for name in config.tools:
                tool = root / name
                tool.touch()
                tool.chmod(0o755)
            for path in inputs:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test")
            with (
                patch.object(cli, "SCRIPT_DIR", root),
                patch.object(cli, "find_tool", return_value=root / "img4tool"),
                patch("src.ios16se.build.load_config", return_value=config),
                patch("src.ios16se.build.get_ipsw_path", side_effect=inputs[:2] * 4),
                patch("src.ios16se.img4_tool.get_img4_tool"),
                patch("src.ios16se.firmware.verify", wraps=verify) as verify_ipsw,
            ):
                self.assertEqual(cli.doctor(), 0)
                verify_ipsw.assert_not_called()
                self.assertEqual(cli.doctor(verify_firmware=True), 1)
                self.assertEqual(
                    [call.args for call in verify_ipsw.call_args_list],
                    [(root / inputs[0], "target-ios15"), (root / inputs[1], "donor-ipados16")],
                )
                (root / inputs[-1]).write_bytes(b"")
                self.assertEqual(cli.doctor(), 1)
                (root / inputs[-1]).write_bytes(b"test")
                (root / "gaster").chmod(0o644)
                self.assertEqual(cli.doctor(), 1)

    def test_logs_continue_without_lsusb_and_keep_each_ssh_result(self):
        def host_run(cmd, **kwargs):
            if cmd[0] == "lsusb":
                raise FileNotFoundError("lsusb")
            if cmd[0] == "idevice_id":
                raise subprocess.TimeoutExpired(cmd, 10, b"partial-id", b"host error")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        def ssh(port, command, timeout=10):
            if command == "echo SSH_OK":
                return 0, "SSH_OK", ""
            if command == "mount":
                raise subprocess.TimeoutExpired(command, timeout, b"mount output", b"SSH error")
            return 0, f"result for {command}", ""

        with tempfile.TemporaryDirectory() as logs, contextlib.redirect_stdout(io.StringIO()):
            with (
                patch.object(cli, "LOGS_DIR", Path(logs)),
                patch.object(cli.sys, "platform", "linux"),
                patch.object(cli, "run", side_effect=host_run),
                patch.object(cli, "ssh_forward", return_value=nullcontext()) as forward,
                patch.object(cli, "ssh_exec", side_effect=ssh) as ssh_exec,
            ):
                cli.capture_logs(wait=3, port=2233)
            report = next(Path(logs).iterdir())
            self.assertIn("lsusb", (report / "lsusb.txt").read_text())
            self.assertTrue((report / "irecovery.txt").is_file())
            host_log = (report / "idevice-id.txt").read_text()
            self.assertIn("partial-id", host_log)
            self.assertIn("host error", host_log)
            self.assertFalse((report / "device-info.txt").exists())
            ssh_log = (report / "ssh.log").read_text()
            self.assertIn("result for sw_vers", ssh_log)
            self.assertIn("=== mount ===", ssh_log)
            self.assertIn("timed out", ssh_log)
            self.assertIn("mount output", ssh_log)
            self.assertIn("SSH error", ssh_log)
            self.assertIn("result for dmesg", ssh_log)
            forward.assert_called_once_with(2233, startup_timeout=3)
            self.assertLessEqual(ssh_exec.call_args_list[0].kwargs["timeout"], 3)
            self.assertTrue(all(call.args[0] == 2233 for call in ssh_exec.call_args_list))

    def test_crash_reports_follow_device_reappearance(self):
        cases = [
            ("", "se", "iPhone8,4", True),
            ("se", "se", "iPhone8,4", True),
            ("se", "different", "iPhone8,4", False),
            ("", "one\ntwo", "iPhone8,4", False),
            ("", "other", "iPhone9,1", False),
        ]
        for before, after, model, expected in cases:
            with self.subTest(before=before, after=after, model=model), tempfile.TemporaryDirectory() as directory:
                ids = iter((before, after))
                copies = []

                def host(cmd, **kwargs):
                    output = ""
                    if cmd[0] == "idevice_id":
                        output = next(ids)
                    elif cmd[0] == "ideviceinfo":
                        output = f"ProductType: {model}\nProductVersion: 15.8.8\n"
                    elif cmd[0] == "idevicecrashreport":
                        self.assertTrue(Path(cmd[-1]).is_dir())
                        copies.append(cmd[1:-1])
                    return subprocess.CompletedProcess(cmd, 0, output.encode(), b"")

                with (
                    patch.object(cli, "LOGS_DIR", Path(directory)),
                    patch.object(cli, "run", side_effect=host),
                    patch.object(cli, "collect_ssh_logs", return_value=False),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    cli.capture_logs(wait=1)
                report = next(Path(directory).iterdir())
                self.assertEqual((report / "idevice-id.txt").read_text(), before)
                self.assertEqual((report / "idevice-id-after.txt").read_text(), after)
                self.assertEqual(copies, [["-u", "se", "-k"]] if expected else [])
                if expected:
                    self.assertIn("15.8.8", (report / "device-info-after.txt").read_text())
                else:
                    self.assertTrue((report / "crash-report.txt").read_text().strip())

    def test_cli_passes_diagnostic_options_and_exit_status(self):
        with (
            patch.object(sys, "argv", ["cli", "logs", "--port", "2233", "--wait", "7"]),
            patch.object(cli, "capture_logs") as capture,
        ):
            cli.main()
        capture.assert_called_once_with(wait=7, port=2233)
        with (
            patch.object(sys, "argv", ["cli", "doctor", "--verify-firmware"]),
            patch.object(cli, "doctor", return_value=1) as doctor,
        ):
            self.assertEqual(cli.main(), 1)
        doctor.assert_called_once_with(verify_firmware=True)

    def test_post_boot_capture_records_stock_return_after_ssh_failure(self):
        # The host sees no usbmux device at first. SSH exits early, then the
        # same test phone becomes visible to the host under stock iOS.
        with tempfile.TemporaryDirectory() as directory:
            observations = iter(["", "", "se"])
            now = [0.0]
            commands = []

            def host(cmd, **kwargs):
                commands.append(cmd)
                out = ""
                if cmd[0] == "idevice_id":
                    out = next(observations)
                if cmd[0] == "ideviceinfo":
                    out = "ProductType: iPhone8,4\nProductVersion: 15.8.8\nBuildVersion: 19H422\nUniqueChipID: 123\n"
                return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")

            with (
                patch.object(cli, "LOGS_DIR", Path(directory)),
                patch.object(cli, "run", side_effect=host),
                patch.object(cli, "collect_ssh_logs", return_value=False),
                patch.object(cli.time, "monotonic", side_effect=lambda: now[0]),
                patch.object(cli.time, "sleep", side_effect=lambda t: now.__setitem__(0, now[0] + t)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                probe = "ECID: 0x7b\nCPID: 0x8000\n"
                self.assertEqual(cli.capture_kernel_logs(port=2233, max_wait=5, recovery_probe=probe), 1)
            report = next(Path(directory).iterdir())
            self.assertTrue((report / "device-info-after.txt").is_file(), "stock reappearance was lost")
            self.assertIn("19H422", (report / "device-info-after.txt").read_text())
            self.assertIn(["idevicecrashreport", "-u", "se", "-k", str(report / "crash")], commands)
            events = (report / "timeline.log").read_text().splitlines()
            self.assertIn("host returned from bootx", events[0])
            self.assertTrue(any("SSH unavailable" in event for event in events))
            self.assertTrue(any("device-info-after.txt" in event for event in events))
            for event in events:
                self.assertEqual(datetime.fromisoformat(event.split()[0]).utcoffset(), timedelta(0))

    def test_post_boot_capture_skips_reports_from_another_se(self):
        def host(cmd, **kwargs):
            outputs = {
                "idevice_id": b"se",
                "ideviceinfo": b"ProductType: iPhone8,4\nUniqueChipID: 124\n",
            }
            return subprocess.CompletedProcess(cmd, 0, outputs[cmd[0]], b"")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(cli, "LOGS_DIR", Path(directory)),
            patch.object(cli, "run", side_effect=host),
            patch.object(cli, "collect_ssh_logs", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.capture_kernel_logs(max_wait=1, recovery_probe="ECID: 0x7b\nCPID: 0x8000\n")
            report = next(Path(directory).iterdir())
            self.assertFalse((report / "crash").exists())
            self.assertIn("Cannot match", (report / "crash-report.txt").read_text())
