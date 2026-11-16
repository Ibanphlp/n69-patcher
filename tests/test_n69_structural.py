"""Structural checks for the N69 iOS 16 boot path.

These tests do not claim the phone reached SpringBoard.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ios16se.kc_patch import BOOT_MODES, patches_for_mode
from ios16se.sep_shim import prologue_was_replaced_with_ret
from ios16se.soc import (
    SocMismatch, allow_ramdisk_sep, allow_seputil_load, ibec_needs_go,
    identity_for, identity_from_probe, require_artifact_identity, stock_data_slot,
)
from ios16se.trustcache import TrustCacheError, build_v1, seal
from ios16se.wifi_hash import MOV_W0_0_RET, MOV_W0_1_RET


class ModeTests(unittest.TestCase):
    def test_modes_apply_different_patches(self):
        minimal = patches_for_mode("minimal")
        safe = patches_for_mode("safe-minimal")
        full = patches_for_mode("full")
        self.assertEqual(minimal, ["generic_security"])
        self.assertIn("pmu_d2255", safe)
        self.assertIn("sep_legacy", safe)
        self.assertNotIn("display_pinot", safe)
        self.assertIn("display_pinot", full)
        self.assertIn("touch_spin69", full)
        self.assertNotEqual(set(minimal), set(safe))
        self.assertNotEqual(set(safe), set(full))
        for mode in BOOT_MODES:
            for blocked in ("audio_cs42l71", "audio_cs35l19", "charger_c26", "wifi_bcm", "vibration_drv2604"):
                self.assertNotIn(blocked, patches_for_mode(mode))


class SocTests(unittest.TestCase):
    def test_model_match_is_exact(self):
        probe = "CPID: 0x8003\nMODEL: n69uap\n"
        self.assertEqual(identity_from_probe(probe).donor_board, "j71tap")
        with self.assertRaises(SocMismatch):
            require_artifact_identity("n69ap", probe)
        with self.assertRaises(SocMismatch):
            require_artifact_identity("n69uap", "CPID: 0x8000\nMODEL: n69ap\n")
        with self.assertRaises(SocMismatch):
            identity_for("n69uap", 0x8000)
        # A substring of the TSMC model must not select the Samsung board.
        with self.assertRaises(SocMismatch):
            identity_from_probe("CPID: 0x8003\nMODEL: n69uap\n".replace("MODEL: n69uap", "MODEL: n69"))
        # The connected TSMC SE reports MODEL n69ap with CPID 0x8003.
        tsmc = identity_from_probe("CPID: 0x8003\nMODEL: n69ap\n")
        self.assertEqual(tsmc.model, "n69uap")
        self.assertEqual(tsmc.donor_board, "j71tap")

    def test_go_and_seputil_and_ramdisk_sep(self):
        self.assertFalse(ibec_needs_go(0x8000))
        self.assertFalse(ibec_needs_go(0x8003))
        self.assertTrue(ibec_needs_go(0x8010))
        self.assertFalse(allow_seputil_load(0x8000))
        self.assertFalse(allow_seputil_load(0x8003))
        self.assertTrue(allow_seputil_load(0x8010))
        self.assertFalse(allow_ramdisk_sep(0x8000, "none"))
        self.assertFalse(allow_ramdisk_sep(0x8003, "none"))
        script = (ROOT / "n69ap_ios16_full_install.sh").read_text()
        self.assertNotIn("N61_RAMDISK_SEP=1", script)
        self.assertIn("N61_RAMDISK_SEP=0", script)
        ssh = (ROOT / "n61-patcher-master/lib/ssh.sh").read_text()
        self.assertNotIn("N61_FORCE_SEPUTIL_LOAD", ssh)
        self.assertIn("device_is_a9", ssh)

    def test_stock_data_slot_uses_role(self):
        volumes = [(1, 1, "SystemA"), (2, 0, "Other"), (3, 0x40, "DataA"), (9, 0x40, "DataB")]
        self.assertEqual(stock_data_slot(volumes, configured=2, data_b=9), 3)
        self.assertEqual(stock_data_slot(volumes, configured=3, data_b=9), 3)
        with self.assertRaises(SocMismatch):
            stock_data_slot([(9, 0x40, "DataB")], configured=None, data_b=9)


class SepAndWifiTests(unittest.TestCase):
    def test_prologue_ret_is_rejected(self):
        before = b"\x00" * 4 + bytes.fromhex("fd7bbfa9") + b"\x11" * 4
        after = b"\x00" * 4 + bytes.fromhex("c0035fd6") + b"\x11" * 4
        self.assertTrue(prologue_was_replaced_with_ret(before, after))
        self.assertFalse(prologue_was_replaced_with_ret(before, before))

    def test_wifi_return_flip_detector(self):
        before = b"pad" + MOV_W0_0_RET + b"end"
        after = b"pad" + MOV_W0_1_RET + b"end"
        flipped = 0
        for index in range(len(before) - 7):
            if before[index:index + 8] == MOV_W0_0_RET and after[index:index + 8] == MOV_W0_1_RET:
                flipped += 1
        self.assertEqual(flipped, 1)


class TrustCacheTests(unittest.TestCase):
    def test_stock_cache_is_not_sealed_for_patched_bins(self):
        stock = build_v1([b"\x11" * 20])
        with self.assertRaises(TrustCacheError):
            seal(stock, [], require_patched=True)
        sealed = seal(stock, [b"\x22" * 20], require_patched=True)
        self.assertNotEqual(sealed, stock)
        self.assertIn(b"\x22" * 20, sealed)


class GuideTests(unittest.TestCase):
    def test_root_is_system_b(self):
        guide = (ROOT / "N69AP_BOOT_GUIDE.md").read_text()
        self.assertNotIn("rd=disk0s1s4", guide)
        profile = (ROOT / "profiles/n69ap_ios16.toml").read_text()
        self.assertIn("rd=disk0s1s8", profile)


if __name__ == "__main__":
    unittest.main()
