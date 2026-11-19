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
apfs_create_volumes() { return 0; }
rsh_timeout() { die "Reached a device write"; }
T_NAME=n69-ios16
T_MNT_SYSTEM_B=8
T_MNT_DATA_B=9
T_VOL_CREATE="SystemB DataB"

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
    def test_volume_preflight_preserves_recovery_and_validates_volume_identity(self):
        cases = {
            "correct-role": None,
            "foreign-recovery": "s3 already holds the Recovery role",
            "wrong-name": "expected 'DataB'",
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
