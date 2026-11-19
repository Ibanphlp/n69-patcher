import contextlib
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


class DiagnosticsTests(unittest.TestCase):
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
                patch("src.ios16se.build.get_ipsw_path", side_effect=inputs[:2] * 3),
                patch("src.ios16se.img4_tool.get_img4_tool"),
            ):
                self.assertEqual(cli.doctor(), 0)
                (root / inputs[-1]).write_bytes(b"")
                self.assertEqual(cli.doctor(), 1)
                (root / inputs[-1]).write_bytes(b"test")
                (root / "gaster").chmod(0o644)
                self.assertEqual(cli.doctor(), 1)

    def test_logs_continue_without_lsusb_and_keep_each_ssh_result(self):
        def host_run(cmd, **kwargs):
            if cmd[0] == "lsusb":
                raise FileNotFoundError("lsusb")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        def ssh(port, command, timeout=10):
            if command == "echo SSH_OK":
                return 0, "SSH_OK", ""
            if command == "mount":
                raise subprocess.TimeoutExpired(command, timeout)
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
            ssh_log = (report / "ssh.log").read_text()
            self.assertIn("result for sw_vers", ssh_log)
            self.assertIn("=== mount ===", ssh_log)
            self.assertIn("timed out", ssh_log)
            self.assertIn("result for dmesg", ssh_log)
            forward.assert_called_once_with(2233, startup_timeout=3)
            self.assertLessEqual(ssh_exec.call_args_list[0].kwargs["timeout"], 3)
            self.assertTrue(all(call.args[0] == 2233 for call in ssh_exec.call_args_list))

    def test_cli_passes_diagnostic_options_and_exit_status(self):
        with (
            patch.object(sys, "argv", ["cli", "logs", "--port", "2233", "--wait", "7"]),
            patch.object(cli, "capture_logs") as capture,
        ):
            cli.main()
        capture.assert_called_once_with(wait=7, port=2233)
        with patch.object(sys, "argv", ["cli", "doctor"]), patch.object(cli, "doctor", return_value=1):
            self.assertEqual(cli.main(), 1)

    def test_checked_live_command_raises_on_nonzero_exit(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            cli.run([sys.executable, "-c", "raise SystemExit(7)"], live=True)
        self.assertEqual(caught.exception.returncode, 7)
