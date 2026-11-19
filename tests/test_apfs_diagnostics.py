import os
import subprocess
import tempfile
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "n61-patcher-master" / "lib"
HARNESS = r"""
set -uo pipefail
source "$1/apfs.sh"
source "$1/stage.sh"
die() { printf '%s\n' "$*" >&2; exit 1; }
apfs_volumes() { return 0; }
dry() { return 1; }
ssh_ensure() { return 0; }
data_is_encrypted() { return 1; }
apfs_vol_exists() { [ -n "${APFS_V_BLOCK[$1]:-}" ]; }
slices() { printf ' /dev/disk0s1s%s' "${APFS_V_IDX[@]}"; }
sync_dev() { :; }

rsh_timeout() {
    [ "$2" = "/sbin/newfs_apfs -A -v SystemB /dev/disk0s1" ] && [ "${FRESH:-0}" = 1 ] ||
        die "Unexpected device write: $2"
    make_block 8 0 SystemB
    FRESH=0
}
log() { :; }
rsh() { :; }
T_NAME=n69-ios16
T_MNT_SYSTEM_B=8
T_VOL_CREATE=SystemB
T_ROLES="8:0x4"

make_block() {
    local idx="$1" role="$2" name="$3" block="$TMPDIR/s$1.bin"
    dd if=/dev/zero of="$block" bs=4096 count=1 2>/dev/null
    printf "\x$(printf '%02x' "$role")" |
        dd of="$block" bs=1 seek=$((APFS_ROLE_OFF)) conv=notrunc 2>/dev/null
    printf '%s\x00residual' "$name" |
        dd of="$block" bs=1 seek=$((APFS_NAME_OFF)) conv=notrunc 2>/dev/null
    apfs_seal "$block"
    APFS_V_IDX[$idx]="$idx"
    APFS_V_BLOCK[$idx]="$block"
}

make_block 1 1 System
make_block 2 64 Data
make_block 8 4 SystemB
case "$2" in
    correct-role) stage_volumes ;;
    fresh)
        unset 'APFS_V_BLOCK[8]' 'APFS_V_IDX[8]'
        for idx in 3 4 5 6 7; do make_block "$idx" 0 Reserved; done
        FRESH=1
        stage_volumes && stage_volumes && [ "$FRESH" = 0 ]
        ;;
    legacy-data) make_block 9 0 DataB; stage_volumes && guard_target_volumes ;;
    foreign-s9) make_block 9 0 Other; stage_volumes ;;
    orphan-s9) unset 'APFS_V_IDX[8]' 'APFS_V_BLOCK[8]'; make_block 9 0 DataB; stage_volumes ;;
    missing-stock-data) make_block 2 0 Data; stage_volumes ;;
    original-reported-layout) make_block 8 0 SystemB; stage_volumes ;;
    unexpected-system-role) make_block 8 1 SystemB; stage_set_roles ;;
    wrong-role-target) make_block 8 0 Other; stage_set_roles ;;
    active-data)
        make_block 9 64 DataB
        stage_volumes
        ;;
    foreign-recovery) make_block 3 4 Recovery; stage_volumes ;;
    wrong-name)
        make_block 9 0 Other
        OPT_RECREATE_DATA=1
        stage_volumes
        ;;
    bad-checksum)
        printf 'corrupt' | dd of="$TMPDIR/s8.bin" conv=notrunc 2>/dev/null
        stage_volumes
        ;;
esac
"""


class ApfsDiagnosticsTests(unittest.TestCase):
    def test_target_storage_profiles_match_shell_layout(self):
        import runpy
        import tomllib
        target = LIB.parent / "targets/n69-ios16"
        mirror = runpy.run_path(str(target.with_suffix(".py")))
        with (LIB.parents[1] / "profiles/n69ap_ios16.toml").open("rb") as source:
            profile = tomllib.load(source)
        system = profile["volumes"]["system_b"]
        self.assertEqual(profile["volumes"]["shared_data"], {"source": "stock", "role": 64})
        self.assertNotIn("data_b", profile["volumes"])
        shell = subprocess.check_output(["bash", "-c",
            'source "$1"; echo "$T_VOL_CREATE $T_ROLES $T_MNT_SYSTEM_B $T_ROLES_DEMOTE_OLD"',
            "target", str(target.with_suffix(".sh"))], text=True).strip()
        expected = [system["name"], f"{system['partition']}:0x{system['role']:x}", str(system["partition"]), "0"]
        self.assertEqual(shell.split(), expected)
        self.assertEqual([str(mirror["T_" + k]) for k in ("VOL_CREATE", "ROLES", "MNT_SYSTEM_B", "ROLES_DEMOTE_OLD")], expected)

    def test_current_guides_identify_shared_data_and_se_evidence(self):
        root = LIB.parents[1]
        for name in ("README_MAC.md", "N69AP_BOOT_GUIDE.md", "FINAL_AUDIT_TABLE.md"):
            doc = (root / name).read_text()
            with self.subTest(name=name):
                self.assertIn("SystemB", doc)
                self.assertIn("s8", doc)
                self.assertIn("0x04", doc)
                self.assertIn("0x40", doc)
                self.assertNotIn("SystemB (s4)", doc)
        audit = (root / "FINAL_AUDIT_TABLE.md").read_text()
        self.assertNotIn("100%", audit)
        self.assertIn("No trace from that attempt", audit)

    def test_preboot_fallback_never_copies_an_ambiguous_sep(self):
        script = r"""
source "$1/stage.sh"
PREBOOT_MNT="$2"
rsh() { bash -c "${1//\/mnt6\/usr\/standalone\/firmware\//\"\$DESTINATION\"}"; }
copy_unique_preboot_sep
"""
        with tempfile.TemporaryDirectory() as directory:
            preboot = Path(directory) / "preboot"
            destination = Path(directory) / "destination"
            destination.mkdir()
            for count in range(3):
                with self.subTest(candidates=count):
                    if count:
                        candidate = preboot / f"firmware {count}" / "usr/standalone/firmware/sep-firmware.img4"
                        candidate.parent.mkdir(parents=True)
                        candidate.write_bytes(bytes([count]))
                    installed = destination / "sep-firmware.img4"
                    installed.write_bytes(b"original")
                    result = subprocess.run(
                        ["bash", "-c", script, "test-preboot", str(LIB), str(preboot)],
                        env={**os.environ, "DESTINATION": str(destination)},
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, int(count != 1), result.stderr)
                    self.assertEqual(installed.read_bytes(), b"\x01" if count == 1 else b"original")
                    if count == 2:
                        self.assertIn("Multiple SEP firmware files", result.stderr)

    def test_volume_preflight_preserves_recovery_and_validates_volume_identity(self):
        cases = {
            "correct-role": None,
            "original-reported-layout": None,
            "unexpected-system-role": "'SystemB' has unexpected role 1",
            "wrong-role-target": "expected 'SystemB'",
            "active-data": "Unexpected Data role at s9",
            "legacy-data": None,
            "foreign-s9": None,
            "fresh": None,
            "orphan-s9": "Unexpected APFS slice",
            "missing-stock-data": "Expected one existing stock Data",
            "foreign-recovery": "s3 already holds the Recovery role",
            "wrong-name": "DataB recreation and encryption are unsupported",
            "bad-checksum": "s8 has an invalid checksum",
        }
        for case, error in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    ["bash", "-c", HARNESS, "test-apfs", str(LIB), case],
                    env={**os.environ, "TMPDIR": directory},
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, int(error is not None), result.stderr)
                if error:
                    self.assertIn(error, result.stderr)
