import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research import audit_n69_transplants as audit_tool


class DriverAuditTests(unittest.TestCase):
    def test_chained_trials_are_private_and_cleaned_after_success_or_failure(self):
        for fail_display in (False, True):
            with self.subTest(fail_display=fail_display), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ipad = root / "ipad"
                se = root / "se"
                ipad.write_bytes(b"ipad")
                se.write_bytes(b"se")
                sentinel = root / "n69-audit-pmu.raw"
                sentinel.write_bytes(b"another user's file")
                sources = []

                def simulated_trial(source, donor, work, old, new, book):
                    sources.append(source.read_bytes())
                    output = work / "output.raw"
                    output.write_bytes(work.name.encode())
                    if fail_display and work.name == "display":
                        raise ValueError("simulated failure")
                    return output

                with (
                    patch.object(tempfile, "tempdir", directory),
                    patch.object(audit_tool, "BindingBook"),
                    patch.object(audit_tool, "transplant_footprint", side_effect=simulated_trial),
                ):
                    report = audit_tool.audit(se, ipad)

                self.assertEqual(sources, [b"ipad", b"pmu"] if fail_display else [b"ipad", b"pmu", b"display"])
                self.assertEqual(report["structural_pass"], not fail_display)
                if fail_display:
                    self.assertIn("simulated failure", report["drivers"]["display"]["trial"])
                    self.assertIn("not run", report["drivers"]["touch"]["trial"])
                self.assertEqual(set(root.iterdir()), {ipad, se, sentinel})
                self.assertEqual(sentinel.read_bytes(), b"another user's file")

    def test_report_cannot_overwrite_an_input_through_a_hard_link(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory) / "kernel"
            kernel.write_bytes(b"original")
            report = Path(directory) / "report.json"
            report.hardlink_to(kernel)
            argv = ["audit", "--se-kernel", str(kernel), "--ipad-kernel", str(kernel), "--write", str(report)]
            with (
                patch.object(sys, "argv", argv),
                patch.object(audit_tool, "audit") as audit,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(audit_tool.main(), 2)
            audit.assert_not_called()
            self.assertEqual(kernel.read_bytes(), b"original")
