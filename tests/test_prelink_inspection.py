import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ios16se import cli, prelink
from ios16se.driver_re import DriverRE


RECORDS = b"""<dict><key>_PrelinkInfoDictionary</key><array>
<dict>
  <key>CFBundleIdentifier</key><string>test.spi</string>
  <key>_PrelinkExecutableLoadAddr</key><integer>0x4000</integer>
  <key>_PrelinkExecutableSize</key><integer>0x60</integer>
  <key>OSBundleLibraries</key><dict><key>test.platform</key><string ID="1">1</string></dict>
</dict>
<dict>
  <key>CFBundleIdentifier</key><string>test.spin</string>
  <key>IOKitPersonalities</key><dict><key>Touch</key><dict>
    <key>CFBundleIdentifier</key><string>test.not-a-bundle</string>
  </dict></dict>
  <key>OSBundleLibraries</key><dict><key>test.spi</key><string IDREF="1"/></dict>
</dict>
<dict>
  <key>CFBundleIdentifier</key><string>test.lcd</string>
  <key>_PrelinkExecutableLoadAddr</key><integer>0x5000</integer>
  <key>_PrelinkExecutableSize</key><integer>126</integer>
  <key>OSBundleLibraries</key><dict IDREF="2"/>
</dict></array><key>Libraries</key><dict ID="2"><key>test.power</key><string>1</string></dict></dict>"""


def kernel(xml):
    header = struct.pack("<8I", 0xfeedfacf, 0, 0, 2, 1, 72, 0, 0)
    segment = struct.pack("<2I16s4Q4I", 25, 72, b"__PRELINK_INFO", 0, len(xml), 104, len(xml), 0, 0, 0, 0)
    return header + segment + xml


class DriverInspectionTests(unittest.TestCase):
    def test_inventory_uses_direct_records_and_resolves_references(self):
        records = prelink.driver_records(RECORDS + b"\0" * 20)
        self.assertEqual(set(records), {"test.spi", "test.spin", "test.lcd"})
        self.assertEqual(records["test.lcd"], prelink.DriverRecord(0x5000, 126, ("test.power",), ()))
        self.assertEqual(records["test.spin"], prelink.DriverRecord(None, None, ("test.spi",), ("Touch",)))

    def test_cli_inspects_personality_and_refuses_to_extract_neighbour(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "kernel"
            image.write_bytes(kernel(RECORDS))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["inspect-driver", str(image), "test.spin"]), 0)
            self.assertIn("Aucun exécutable déclaré", out.getvalue())
            self.assertIn("Dépendances : test.spi", out.getvalue())
            self.assertIn("Personnalités IOKit : Touch", out.getvalue())
            destination = Path(directory) / "existing"
            destination.write_bytes(b"keep")
            for name in ("test.spin", "test.lcd"):
                with self.subTest(name=name), self.assertRaises(prelink.PrelinkError):
                    prelink.extract_driver(image, name, destination)
                self.assertEqual(destination.read_bytes(), b"keep")

    def test_ambiguous_or_malformed_records_are_rejected(self):
        for xml in (
            RECORDS.replace(b"test.lcd", b"test.spi"),
            RECORDS.replace(b'<dict IDREF="2"/>', b'<dict IDREF="missing"/>'),
            RECORDS.replace(b'<dict IDREF="2"/>', b'<dict ID="3" IDREF="3"/>'),
            RECORDS.replace(b"<key>_PrelinkExecutableSize</key><integer>126</integer>", b""),
        ):
            with self.subTest(xml=xml), self.assertRaises(prelink.PrelinkError):
                prelink.driver_records(xml)

    def test_standalone_inventory_and_failed_extraction_preserve_codeless_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "kernel"
            image.write_bytes(kernel(RECORDS))
            reader = DriverRE(image, root / "work")
            entry = next(item for item in reader.list_drivers() if item.bundle_id == "test.spin")
            self.assertIsNone(entry.size)
            self.assertEqual(entry.dependencies, ["test.spi"])
            self.assertNotIn("_PrelinkExecutableSize", reader.find_driver_in_prelink("test.spin"))
            output = reader.work_dir / "test.spin.kext"
            output.mkdir()
            saved = output / "notes"
            saved.write_text("keep")
            with self.assertRaises(prelink.PrelinkError):
                reader.extract_driver("test.spin")
            self.assertEqual(saved.read_text(), "keep")
