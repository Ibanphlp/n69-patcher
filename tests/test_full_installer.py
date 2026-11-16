import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FullInstallerTests(unittest.TestCase):
    def run_installer(self, *, captured=False, ramdisk=True, fail_capture=False, fail_verify=False, resume=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "events"

            def write(path, content, executable=False):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                if executable:
                    target.chmod(0o755)

            script = (ROOT / "n69ap_ios16_full_install.sh").read_text()
            # Exercise the real sequence without giving test stubs root privileges.
            script = script.replace('if [[ $EUID -ne 0 ]]; then', 'if false; then', 1)
            write("installer.sh", script, True)
            write("tools/SSHRD_Script/Darwin/irecovery", "#!/bin/bash\nif [[ $1 == -q ]]; then printf 'MODEL: n69ap\\nMODE: DFU\\nCPID: 0x8003\\nECID: 0x123\\nPWND\\n'; fi\n", True)
            for tool in ("gaster", "img4"):
                write("tools/SSHRD_Script/Darwin/" + tool, "#!/bin/bash\nexit 0\n", True)
            write("ramdisk-main/Darwin/img4tool", "#!/bin/bash\nexit 0\n", True)
            for name in ("iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw", "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"):
                write("firmware/" + name, "firmware")
            components = ("iBSS", "iBEC", "ramdisk", "devicetree", "trustcache", "kernelcache")
            if ramdisk:
                for component in components:
                    write("ramdisk-main/sshramdisk/" + component + ".img4", "image")
            else:
                write("ramdisk-main/sshrd.sh", "#!/bin/bash\nprintf 'generate:%s\\n' \"$1\" >> \"$TEST_LOG\"\nmkdir -p sshramdisk\nfor name in iBSS iBEC ramdisk devicetree trustcache kernelcache; do printf image > \"sshramdisk/$name.img4\"; done\n", True)
            if captured:
                write("state/sep-firmware.img4", "captured")
                write("state/IM4M.bin", "ticket")
            write("n61.sh", """#!/bin/bash
printf '%s:%s\n' "$1" "${N61_RAMDISK_SEP:-}" >> "$TEST_LOG"
if [[ $1 == capture-sep ]]; then
    [[ ${FAIL_CAPTURE:-0} != 1 ]] || exit 1
    mkdir -p state
    printf captured > state/sep-firmware.img4
    printf ticket > state/IM4M.bin
fi
""", True)
            write("prepare_derived_n69.sh", """#!/bin/bash
printf 'build:%s\n' "$1" >> "$TEST_LOG"
mkdir -p artifacts/boot
for name in boot_iBSS boot_iBEC boot_DT boot_TC boot_KC; do printf image > "artifacts/boot/$name.img4"; done
cp state/sep-firmware.img4 artifacts/boot/sep.img4
""", True)
            write("bin/python3", """#!/bin/bash
cat >/dev/null
printf 'verify\n' >> "$TEST_LOG"
[[ ${FAIL_VERIFY:-0} != 1 ]]
""", True)
            write("bin/sleep", "#!/bin/bash\nexit 0\n", True)
            env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                       TEST_LOG=str(log), N69_EXPERIMENTAL_INSTALL_ACK="1",
                       FAIL_CAPTURE=str(int(fail_capture)), FAIL_VERIFY=str(int(fail_verify)))
            command = ["bash", str(root / "installer.sh"), "full"]
            if resume:
                command.append("--resume-ramdisk")
            result = subprocess.run(command, cwd=root, env=env,
                                    input="\n" * 5, capture_output=True, text=True, timeout=30)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_missing_capture_is_automatic_and_precedes_build(self):
        result, events = self.run_installer(ramdisk=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[:6], ["generate:15.8.8", "ramdisk:0", "capture-sep:", "verify", "build:full", "ramdisk:0"])
        self.assertIn("volumes:", events)

    def test_resume_captures_without_reboot_and_verifies_before_build(self):
        result, events = self.run_installer(ramdisk=False, resume=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[:5], ["capture-sep:", "verify", "generate:15.8.8", "build:full", "ramdisk:0"])
        self.assertIn("volumes:", events)

    def test_resume_capture_failure_never_builds_or_writes_volumes(self):
        result, events = self.run_installer(resume=True, fail_capture=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["capture-sep:"])

    def test_resume_existing_capture_still_requires_live_device_verification(self):
        result, events = self.run_installer(resume=True, captured=True, fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["verify"])

    def test_existing_capture_is_verified_before_build(self):
        result, events = self.run_installer(captured=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[:3], ["verify", "build:full", "ramdisk:0"])
        self.assertNotIn("capture-sep:", events)

    def test_capture_failure_never_builds_or_writes_volumes(self):
        result, events = self.run_installer(fail_capture=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["ramdisk:0", "capture-sep:"])

    def test_wrong_device_stops_before_build_and_writes(self):
        result, events = self.run_installer(captured=True, fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["verify"])

    def test_n69_reticket_and_sep_precede_trustcache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ramdisk = root / 'ramdisk'
            ramdisk.mkdir()
            for name in ('iBSS', 'iBEC', 'ramdisk', 'devicetree', 'trustcache', 'kernelcache'):
                (ramdisk / (name + '.img4')).write_bytes(b'image')
            sep = root / 'sep.img4'
            sep.write_bytes(b'sep')
            script = r'''
source "$LIB"
need_dfu() { :; }; identity_guard() { :; }; verify_n69_sep_device() { :; }
pwn_device() { :; }; wait_irecovery_stage() { :; }; ssh_wait() { :; }
sleep() { :; }; step() { :; }; log() { :; }; im4m_path() { printf '%s' ticket; }
die() { printf 'stopped\n'; exit 1; }
python3() { printf 'reticket\n'; [ "${FAIL_BUNDLE:-0}" = 0 ]; }
irec_checked() { printf '%s %s\n' "$1" "$2"; }
send_ramdisk_chain
'''
            env = dict(os.environ, LIB=str(ROOT / 'n61-patcher-master/lib/device.sh'),
                       REPO=str(root), T_RAMDISK_DIR='ramdisk', WK=str(root / 'work'),
                       RAMDISK_SEP=str(sep), T_NAME='n69-ios16')
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            events = result.stdout.splitlines()
            self.assertEqual(events[0], 'reticket')
            self.assertLess(events.index('-c devicetree'), events.index('-c rsepfirmware'))
            self.assertLess(events.index('-c rsepfirmware'), events.index('-c firmware'))
            self.assertLess(events.index('-c firmware'), events.index('-c bootx'))
            self.assertIn(str(root / 'work/ramdisk-device-ticket/iBSS.img4'), result.stdout)
            env['FAIL_BUNDLE'] = '1'
            failed = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('-f ', failed.stdout)

    def test_ramdisk_sep_transfer_output_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ramdisk = root / 'ramdisk'
            ramdisk.mkdir()
            for name in ('iBSS', 'iBEC', 'ramdisk', 'devicetree', 'trustcache', 'kernelcache'):
                (ramdisk / (name + '.img4')).write_bytes(b'image')
            sep = root / 'captured-sep.img4'
            sep.write_bytes(b'sep')
            script = r'''
source "$LIB"
need_dfu() { :; }
identity_guard() { :; }
verify_n69_sep_device() { :; }
pwn_device() { :; }
wait_irecovery_stage() { :; }
ssh_wait() { :; }
sleep() { :; }
step() { :; }
log() { :; }
irec_checked() {
    if [[ "$1" == -f && "$2" == "$RAMDISK_SEP" ]]; then echo transfer-output; fi
    if [[ "$1" == -c && "$2" == rsepfirmware ]]; then echo command-output; fi
}
send_ramdisk_chain
'''
            env = dict(os.environ, LIB=str(ROOT / 'n61-patcher-master/lib/device.sh'),
                       REPO=str(root), T_RAMDISK_DIR='ramdisk', WK=str(root / 'work'),
                       RAMDISK_SEP=str(sep))
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('transfer-output', result.stdout)
            self.assertIn('command-output', result.stdout)

    def test_failed_sep_ping_reports_uncertainty_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.ramdisk-sep-sent').write_text('captured-sep.img4')
            script = r'''
source "$LIB"
sep_ping_ok() { return 1; }
log() { :; }
sleep() { :; }
die() { printf '%s\n' "$*" >&2; exit 1; }
ensure_sep
echo unexpected-continued
'''
            env = dict(os.environ, LIB=str(ROOT / 'n61-patcher-master/lib/ssh.sh'), WK=str(root))
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cause is unknown', result.stderr)
            self.assertIn('Do not use seputil --load on A9', result.stderr)
            self.assertNotIn('unexpected-continued', result.stdout)


if __name__ == "__main__":
    unittest.main()
