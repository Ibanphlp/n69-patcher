import os
import subprocess
import tempfile
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "n61-patcher-master" / "lib" / "apfs.sh"
HARNESS = r"""
set -uo pipefail
source "$1"
die() { printf '%s\n' "$*" >&2; exit 1; }
log() { printf '%s\n' "$*"; }
ok() { log "$*"; }
rsh() { return 0; }
apfs_volumes() { return 0; }
apfs_list() { return 0; }
apfs_write_block() {
    [ "$(find "$WK" -name 'block-*.bin' | wc -l)" -eq 2 ] || die "Missing backups"
    printf '%s\n' "$1" >> "$WK/writes"
    [ "$1" != 109 ] || die "Simulated second write failure"
    cp "$2" "$WK/readback-$1"
}
apfs_read_block_fresh() { printf '%s\n' "$WK/readback-$1"; }

make_block() {
    local idx="$1" role="$2" name="$3"
    local blk="$WK/s$idx.bin"
    dd if=/dev/zero of="$blk" bs=4096 count=1 2>/dev/null
    printf "\\x$(printf '%02x' "$role")" | dd of="$blk" bs=1 seek=$((APFS_ROLE_OFF)) conv=notrunc 2>/dev/null
    printf '%s' "$name" | dd of="$blk" bs=1 seek=$((APFS_NAME_OFF)) conv=notrunc 2>/dev/null
    apfs_seal "$blk"
    APFS_V_IDX[$idx]="$idx"
    APFS_V_PADDR[$idx]="$((idx + 100))"
    APFS_V_BLOCK[$idx]="$blk"
}

make_block 8 0 SystemB
make_block 9 0 DataB
case "$2" in
    conflict) make_block 1 1 System ;;
    bad-checksum) printf 'corrupt' | dd of="$WK/s9.bin" conv=notrunc 2>/dev/null ;;
    recovery)
        make_block 8 4 SystemB
        T_MNT_SYSTEM_B=8
        guard_target_volumes system
        ;;
    wrong-name)
        make_block 8 4 Xystem
        T_MNT_SYSTEM_B=8
        guard_target_volumes system
        ;;
esac
apfs_set_roles "8:0x1 9:0x40" 1
"""


class ApfsGuardTests(unittest.TestCase):
    def run_case(self, case, work):
        return subprocess.run(
            ["bash", "-c", HARNESS, "test-apfs", str(LIB), case],
            env={**os.environ, "WK": str(work), "TMPDIR": str(work)},
            text=True, capture_output=True, timeout=15,
        )

    def test_conflict_bad_checksum_and_recovery_are_rejected_before_writes(self):
        cases = {
            "conflict": "s1 already holds it",
            "bad-checksum": "s9:",
            "recovery": "Recovery role",
            "wrong-name": "expected 'SystemB'",
        }
        for case, reason in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                result = self.run_case(case, work)
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertFalse((work / "writes").exists())

    def test_backups_survive_a_simulated_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            result = self.run_case("partial", work)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Simulated second write failure", result.stderr)
            self.assertEqual((work / "writes").read_text().split(), ["108", "109"])
            backup = next(work.glob("apfs-role-backup.*"))
            for index in (8, 9):
                self.assertEqual(
                    (backup / f"block-{100 + index}-s{index}.bin").read_bytes(),
                    (work / f"s{index}.bin").read_bytes(),
                )
