import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

from research.package_audit import FILES


ROOT = Path(__file__).resolve().parents[1]


class PortablePackageTests(unittest.TestCase):
    def test_entry_points_package_only_public_sources_and_fail_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source tree"
            for name in (*FILES, "create_mobile_package.sh", "create_mac_package.sh", "research/package_audit.py"):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
            for name in ("state/IM4M.bin", "artifacts/sep.img4", "src/ios16se/PRIVATE.py",
                         "SevenUP-main/src/iphone7_port/PRIVATE.py", "logs/old.ips"):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"PRIVATE_TICKET_FIXTURE")
            output = base / "audit package.tar.gz"
            for script in ("create_mobile_package.sh", "create_mac_package.sh"):
                with self.subTest(script=script):
                    subprocess.run([source / script, output], cwd=base, check=True, capture_output=True)
                    extracted = base / script
                    with tarfile.open(output) as archive:
                        self.assertEqual(set(archive.getnames()), set(FILES))
                        for member in archive.getmembers():
                            self.assertTrue(member.isfile())
                            self.assertNotIn(b"PRIVATE_TICKET_FIXTURE", archive.extractfile(member).read())
                        archive.extractall(extracted)
                    result = subprocess.run(
                        [sys.executable, "-I", extracted / "research/audit_n69_transplants.py", "--help"],
                        cwd=base, capture_output=True, text=True, check=True)
                    self.assertIn("--compare-spin69", result.stdout)
            previous = output.read_bytes()
            required = source / "SevenUP-main/src/iphone7_port/kernel_transplant.py"
            required.unlink()
            for indirect in (False, True):
                if indirect:
                    os.symlink(source / "state/IM4M.bin", required)
                result = subprocess.run([source / "create_mobile_package.sh", output], cwd=base, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_bytes(), previous)
