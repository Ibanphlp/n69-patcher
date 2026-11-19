import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import ios16se_cli as cli
from src.ios16se import diagnostics as d


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_manifest_states_and_six_artifact_hashes(self):
        image = self.root / d.IMAGE_NAMES["KernelCache"]
        image.write_bytes(b"fixture")
        manifest = self.root / "boot.json"
        for contents, mode, state in [
            (None, "full", "unavailable"), ("{", "full", "unavailable"), ("[]", "full", "unavailable"),
            ("{}", "minimal", "invalid_mode"), ('{"kernelcache_mode":"minimal"}', None, "requested_mode_unknown"),
            ('{"kernelcache_mode":"minimal"}', "full", "mismatch"),
            ('{"kernelcache_mode":"full"}', "full", "match"),
        ]:
            with self.subTest(state=state):
                if contents is not None:
                    manifest.write_text(contents)
                ctx = d.capture_context(self.root, self.root, mode, "standalone_snapshot")
                self.assertEqual(ctx["mode_check"], state)
                self.assertEqual(ctx["image_sha256"]["KernelCache"], hashlib.sha256(b"fixture").hexdigest())
                self.assertIsNone(ctx["image_sha256"]["SEP"])
                self.assertEqual(set(d.IMAGE_NAMES), set(ctx["image_sha256"]))
                self.assertIsNone(ctx["commit"])

    def test_snapshot_separates_profile_manifest_and_last_host_rootfs_receipt(self):
        registry = Path("n61-patcher-master/patches/registry_n69_ios16.tsv")
        project = self.root / "n61-patcher-master"
        (project / "patches").mkdir(parents=True)
        (self.root / registry).write_bytes(registry.read_bytes())
        receipt = project / "build/n69-ios16-latest/patches-install.txt"
        receipt.parent.mkdir(parents=True)
        row = "rootfs\toverlay\trootfs.n69_model_files\tcore\n"
        receipt.write_text(row)
        manifest = self.root / "boot.json"
        for mode in d.MODES:
            patches = list(d.BOOT_MODES[mode])
            manifest.write_text(json.dumps({"kernelcache_mode": mode, "kernel_patches": patches}))
            context = d.capture_context(self.root, self.root, mode, "standalone_snapshot", question="pmu")
            report = d.write_evidence(self.root, False, None, None, context)
            shared = d.public_evidence(report)["host_context"]
            self.assertEqual(shared["kernel_patches"], patches)
            self.assertEqual(shared["host_rootfs_selection"]["groups"], ["core"])
            self.assertEqual(shared["host_rootfs_selection"]["resources"], ["rootfs.n69_model_files"])
            self.assertEqual(shared["question"], "pmu")
            self.assertIn("unassigned to this SE", d.summary(report))
        receipt.write_text(row.replace("core", "PRIVATE"))
        manifest.write_text('{"kernelcache_mode":"full"}')
        legacy = d.capture_context(self.root, self.root, "full", "standalone_snapshot")
        self.assertIsNone(legacy["kernel_patches"])
        self.assertIsNone(legacy["host_rootfs_selection"])

    def test_shell_dry_run_reports_actual_core_selection(self):
        import subprocess

        result = subprocess.run([str(cli.SCRIPT_DIR / "n61.sh"), "patch-rootfs", "--target", "n69-ios16",
                                 "--features", "core", "--dry-run", "-y"], cwd=self.root,
                                check=True, capture_output=True, text=True)
        resources = [line.split()[0] for line in result.stdout.splitlines() if line.startswith("  rootfs.")]
        self.assertEqual(resources, ["rootfs.base_stream", "rootfs.apticket", "rootfs.n69_model_files",
                                     "rootfs.finish", "rootfs.set_roles"])

    def test_live_donor_response_is_a_reference_only_for_matched_connected_capture(self):
        ctx = {"attempt_id": "f26fbbdd-0000-431a-b000-123456789abc", "mode_check": "match",
               "artifact_scope": "pre_send_snapshot", "requested_mode": "full", "manifest_kernelcache_mode": "full",
               "snapshot_at_utc": "2020-01-01T10:00:00Z", "bootx_sent_at_utc": "2020-01-01T10:01:00Z",
               "image_sha256": {name: "a" * 64 for name in d.IMAGE_NAMES}}
        donor = "ProductType: iPhone8,4\nBuildVersion: 20H392\n"
        for changed, info, matched, expected in [
            ({}, donor, True, True), ({}, donor, False, False),
            ({}, donor.replace("20H392", "19H422"), True, False),
            ({"artifact_scope": "standalone_snapshot"}, donor, True, False),
            ({"image_sha256": {}}, donor, True, False),
            ({"mode_check": "mismatch"}, donor, True, False),
            ({}, donor + "BuildVersion: 19H422\n", True, False),
        ]:
            with self.subTest(changed=changed, matched=matched, info=info):
                report = d.write_evidence(self.root, True, info, matched, {**ctx, **changed})
                self.assertEqual(bool(report["reference"]), expected)
                self.assertEqual(bool(d.public_evidence(report)["reference"]), expected)
                self.assertEqual(report["donor_kernel_entry"], "unproven")
                if expected:
                    self.assertEqual(report["reference"]["response_sha256"], hashlib.sha256(info.encode()).hexdigest())

    def test_ips_classification_never_assigns_an_attempt(self):
        first, last = "2026-11-19T08:00:00Z", "2026-11-19T08:05:00Z"
        for when, relation in [
            ("2026-11-19 09:01:00 +0100", "within"), ("2026-10-01T10:00:00Z", "before"),
            ("2026-11-20T00:00:00Z", "after"), ("2026-11-19 08:03:00", "unknown"),
        ]:
            path = self.root / "report.ips"
            path.write_text(json.dumps({"timestamp": when, "os_version": "iOS (20H392)"}) + "\n" +
                            json.dumps({"panicString": "userspace watchdog timeout: no successful checkins from SpringBoard"}))
            report = d.read_report(path, last, first)
            self.assertEqual(report["time_relation"], relation)
            self.assertEqual(report["watchdog_service"], "SpringBoard")
            self.assertEqual(report["reported_build"], "20H392")
            self.assertEqual(report["attribution"], "unassigned")

    def test_partial_header_legacy_and_unreadable_report(self):
        path = self.root / "broken.ips"
        path.write_text('{"timestamp":"2026-11-19 10:00:00 +0200"}\n{"panicString":')
        self.assertEqual(d.read_report(path, None)["parse_status"], "partial")
        path.write_text("Date/Time: 2026-11-19 10:00:00 +0200\nOS Version: iOS (19H422)\nPanic Reason: reset")
        report = d.read_report(path, None)
        self.assertEqual((report["reported_build"], report["panic_string"]), ("19H422", "reset"))
        self.assertIsNone(d.read_report(self.root / "missing", None)["panic_string"])

    def test_summary_compare_and_private_archive_via_cli(self):
        first, second = (self.root / name for name in ("run1", "run2"))
        for directory, payload in ((first, b"first"), (second, b"second")):
            directory.mkdir()
            (directory / "boot_KC.img4").write_bytes(payload)
            crash = directory / "crash"
            crash.mkdir()
            (crash / "PRIVATE-UDID.ips").write_text(json.dumps({"panicString":"PRIVATE-NAME"}))
            (directory / "ssh.log").write_text("PRIVATE-CREDENTIAL")
            (directory / "IM4M.bin").write_bytes(b"PRIVATE-TICKET")
            (directory / "timeline.log").write_text("2026-11-19T10:00:00Z SSH unavailable\n"
                                                    "2026-11-19T10:01:00Z PRIVATE-ECID\n")
            ctx = d.capture_context(self.root, directory, "full", "standalone_snapshot")
            d.write_evidence(directory, False, "ProductType: iPhone8,4\nBuildVersion: 19H422\n", None, ctx)
        archive = self.root / "share.tar.gz"
        for args, expected in [
            (["summary", str(first)], "PRIVATE-NAME"), (["compare", str(first), str(second)], "KernelCache: different"),
            (["share", str(first), str(archive)], "Shared archive"),
        ]:
            with patch.object(sys, "argv", ["cli", *args]), contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main()
                self.assertIn(expected, output.getvalue())
        with tarfile.open(archive) as bundle:
            self.assertEqual(set(bundle.getnames()), {"evidence.json", "summary.txt", "timeline.log"})
            for entry in bundle.getmembers():
                content = bundle.extractfile(entry).read()
                self.assertNotIn(b"PRIVATE", content)
                if entry.name == "timeline.log":
                    self.assertIn(b"SSH unavailable", content)
        with self.assertRaises(FileExistsError):
            d.share_capture(first, archive)

    def test_legacy_and_future_format(self):
        evidence = self.root / "evidence.json"
        old = {"stored_reports": [], "donor_kernel_entry": "observed via SSH uname"}
        evidence.write_text(json.dumps(old))
        self.assertIn("First unconfirmed", d.summary(d.load_capture(self.root)))
        old["schema_version"] = 999
        evidence.write_text(json.dumps(old))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            d.load_capture(self.root)

    def test_shell_and_ui_use_the_shared_diagnostics_entry(self):
        import n69_ui
        import subprocess
        from src.ios16se.diagnostic_coverage import coverage

        self.assertEqual(n69_ui.diagnostic_command("safe-minimal"),
                         [sys.executable, str(cli.SCRIPT_DIR / "ios16se_cli.py"), "logs", "--mode", "safe-minimal"])
        actual = subprocess.check_output([str(cli.SCRIPT_DIR / "n69ui"), "coverage"], cwd=self.root, text=True)
        self.assertEqual(actual.strip(), coverage(cli.SCRIPT_DIR))
        self.assertIn("D2255 PMU | off | selected | selected", actual)
        self.assertIn("SPIN69 | off | off | selected | not tested (personality only)", actual)
        self.assertIn("CS42L71 | off | off | off", actual)

    def test_capture_survives_failure_before_handoff(self):
        from types import SimpleNamespace

        args = SimpleNamespace(mode="minimal", im4m=None)
        with (
            patch.object(cli, "LOGS_DIR", self.root),
            patch.object(cli, "build_images", side_effect=SystemExit(1)),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            cli.boot_device(args, capture_logs=True)
        report = d.load_capture(next(self.root.iterdir()))
        self.assertTrue(report["attempt_id"])
        self.assertEqual(report["capture_status"], "incomplete")
        self.assertIsNone(report["ssh_connected"])
        self.assertIn("host bootx return unknown", d.summary(report))


class LiveRunFailureTests(unittest.TestCase):
    """A failing live subprocess must not be reported as success.

    build_images ran the builder with live=True; run() returned the exit code
    without raising, so a BuildError (e.g. a n69uap phone against an n69ap
    artifact set) printed "Build complete" and the caller then sent the stale
    images from the previous build.
    """

    def test_live_run_raises_on_nonzero_exit(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            cli.run([sys.executable, "-c", "import sys; sys.exit(3)"], live=True)
        self.assertEqual(caught.exception.returncode, 3)

    def test_live_run_returns_code_when_check_disabled(self):
        self.assertEqual(cli.run([sys.executable, "-c", "import sys; sys.exit(3)"],
                                 check=False, live=True), 3)

    def test_live_run_succeeds_on_zero_exit(self):
        self.assertEqual(cli.run([sys.executable, "-c", "pass"], live=True), 0)
