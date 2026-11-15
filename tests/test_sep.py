import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ios16se import sep
from ios16se.build import BuildError, build_sep, verify_sep_for_device


def der(tag, data):
    size = len(data)
    encoded = bytes([size]) if size < 128 else bytes([0x81, size])
    return bytes([tag]) + encoded + data


def image_and_ticket(image_type=b"sepi"):
    string = lambda value: der(0x16, value)
    ticket = der(0x30, string(b"IM4M") + der(4, b"ticket"))
    payload = der(0x30, string(b"IM4P") + string(image_type) + string(b"SEP") + der(4, b"firmware"))
    return der(0x30, string(b"IMG4") + payload + der(0xA0, ticket)), ticket


class SepTests(unittest.TestCase):
    def test_selects_active_and_refuses_ambiguous_or_wrong_slot(self):
        a, b = "A" * 96, "B" * 96
        paths = [f"/mnt8/{slot}/usr/standalone/firmware/sep-firmware.img4" for slot in (a, b)]
        self.assertEqual(sep.select_sep(paths, b.lower()), paths[1])
        self.assertEqual(sep.select_sep(paths, "", a), paths[0])
        with self.assertRaises(sep.SepError):
            sep.select_sep(paths, "")
        with self.assertRaises(sep.SepError):
            sep.select_sep(paths, "", "C" * 96)

    def test_cli_reports_ambiguous_candidates_without_selecting(self):
        slots = ["A" * 96, "B" * 96]
        paths = [f"/mnt8/{slot}/usr/standalone/firmware/sep-firmware.img4" for slot in slots]
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/sep_image.py"), "select", "--active", "", "--stdin"],
            input="\n".join(paths), capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Preboot active value (up to 512 characters): ''", result.stderr)
        self.assertIn("SEP paths found: 2", result.stderr)
        for path in paths:
            self.assertIn(path, result.stderr)
        self.assertIn("No SEP was selected", result.stderr)

    def test_embedded_ticket_slot_and_type_validation(self):
        data, ticket = image_and_ticket()
        slot = hashlib.sha384(ticket).hexdigest().upper()
        image = sep.validate_image(data, expected_ticket_hash=slot)
        self.assertEqual(image.ticket, ticket)
        self.assertEqual(image.preboot_hash, slot)
        with self.assertRaises(sep.SepError):
            sep.validate_image(data, expected_ticket_hash="A" * 96)
        with self.assertRaises(sep.SepError):
            sep.validate_image(image_and_ticket(b"ibss")[0])
        with self.assertRaises(sep.SepError):
            sep.validate_image(data[:-1])
        with self.assertRaises(sep.SepError):
            sep.validate_image(der(0x30, der(0x16, b"IMG4") + der(0x30, b"") + der(0xA0, ticket)))

    def test_build_copies_entire_img4_and_rejects_wrong_ticket(self):
        data, ticket = image_and_ticket()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, ticket_file = root / "source", root / "sep.img4", root / "IM4M.bin"
            source.write_bytes(data)
            ticket_file.write_bytes(ticket)
            config = SimpleNamespace(output={"sep_source": str(source), "boot_sep": str(output)})
            build_sep(config, root, ticket_file)
            self.assertEqual(output.read_bytes(), data)
            ticket_file.write_bytes(b"wrong")
            with self.assertRaises(BuildError):
                build_sep(config, root, ticket_file)
            self.assertEqual(output.read_bytes(), data)

    def test_verify_live_device_and_reject_altered_output(self):
        data, ticket = image_and_ticket()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, ticket_file = root / "source", root / "output", root / "ticket"
            source.write_bytes(data)
            output.write_bytes(data)
            ticket_file.write_bytes(ticket)
            config = SimpleNamespace(output={"sep_source": str(source)}, tools={"img4tool": "tool"})
            with patch("ios16se.sep.ticket_identity", return_value=(123, 32771)):
                verify_sep_for_device(config, ticket_file, output, "ECID: 0x7b\nCPID: 0x8003\n")
                with self.assertRaises(BuildError):
                    verify_sep_for_device(config, ticket_file, output, "ECID: 0x7c\nCPID: 0x8003\n")
                output.write_bytes(data + b"x")
                with self.assertRaises(BuildError):
                    verify_sep_for_device(config, ticket_file, output, "ECID: 0x7b\nCPID: 0x8003\n")

    def test_shell_capture_publishes_original_and_preserves_previous_on_failure(self):
        data, ticket = image_and_ticket()
        slot = hashlib.sha384(ticket).hexdigest().upper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "state").mkdir()
            (root / "tools").symlink_to(ROOT / "tools", target_is_directory=True)
            (root / "src").symlink_to(ROOT / "src", target_is_directory=True)
            tool = root / "img4tool"
            tool.write_text("#!/bin/sh\nprintf 'ECID: ECID: 123\\nCHIP: CHIP: 32771\\n'\n")
            tool.chmod(0o755)
            source = root / "remote.img4"
            source.write_bytes(data)
            script = r'''
                source "$STAGE"
                T_NAME=n69-ios16; PREBOOT_MNT=/mnt8
                dry() { return 1; }; ssh_ensure() { :; }; preboot_slot() { printf '6'; }
                rsh() { :; }; mount_vol() { :; }; log() { :; }; ok() { :; }
                tool() { printf '%s' "$FAKE_TOOL"; }; die() { printf '%s\n' "$*" >&2; exit 1; }
                ssh_run_timeout() {
                    case "$2" in
                        *"/find "*) printf '%s\n' "/mnt8/$SLOT/usr/standalone/firmware/sep-firmware.img4" ;;
                        *"/bin/cat '/mnt8/"*) /bin/cat "$SOURCE" ;;
                        *"/active"*) printf '%s\n' "$SLOT" ;;
                        *) return 1 ;;
                    esac
                }
                stage_capture_sep
            '''
            import os
            env = dict(os.environ, REPO=str(root / "repo"), STAGE=str(ROOT / "n61-patcher-master/lib/stage.sh"),
                       FAKE_TOOL=str(tool), SOURCE=str(source), SLOT=slot)
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "state/sep-firmware.img4").read_bytes(), data)
            self.assertEqual((root / "state/IM4M.bin").read_bytes(), ticket)
            source.write_bytes(data[:-1])
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "state/sep-firmware.img4").read_bytes(), data)
            self.assertEqual((root / "state/IM4M.bin").read_bytes(), ticket)

    def test_cli_capture_validates_before_writing(self):
        data, ticket = image_and_ticket()
        slot = hashlib.sha384(ticket).hexdigest().upper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "image"
            source.write_bytes(data)
            tool = root / "img4tool"
            tool.write_text("#!/bin/sh\nprintf 'ECID: ECID: 123\\nCHIP: CHIP: 32771\\n'\n")
            tool.chmod(0o755)
            target, receipt = root / "ticket", root / "receipt.json"
            command = [sys.executable, str(ROOT / "tools/sep_image.py"), "validate", "--image", str(source),
                       "--source", f"/mnt8/{slot}/usr/standalone/firmware/sep-firmware.img4",
                       "--img4tool", str(tool), "--ticket-out", str(target), "--receipt-out", str(receipt)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_bytes(), ticket)
            self.assertIn('"ecid": "123"', receipt.read_text())
            source.write_bytes(data[:-1])
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), ticket)


if __name__ == "__main__":
    unittest.main()
