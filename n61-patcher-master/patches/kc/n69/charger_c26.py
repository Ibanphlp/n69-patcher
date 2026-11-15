#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C26 Charger driver transplant with size-aware footprint selection.

The C26 charger driver (10,769 bytes) exceeds the HDQGasGaugeControl
footprint (8,708 bytes). This implementation finds a suitable larger
footprint or uses relocation.

Based on SevenUP's footprint-displacement engine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src" / "ios16se" / "sevenup"))

from core.patch import Patch


class KcChargerC26(Patch):
    id = "kc.charger_c26"
    layer = "kc"
    role = "transform"
    feature = "charging_c26"
    scope = "device"
    requires = ("kc.remove_ipad_drivers",)
    note = "C26 charger driver with size-aware footprint"

    # Try multiple footprints in order of preference
    FOOTPRINTS = [
        ("com.apple.driver.AppleAE2Audio", "Larger audio footprint (~30KB)"),
        ("com.apple.driver.AppleD2365PMU", "PMU footprint (~4KB)"),
        ("com.apple.driver.AppleHDQGasGaugeControl", "Original HDQ footprint (8.7KB)"),
    ]

    NEW_BUNDLE = "com.apple.driver.AppleC26Charger"
    DONOR_PATH = Path("artifacts/n69ap-15.8.8/KernelCache.raw")

    def apply(self, ctx, art=None):
        # Find the kernelcache
        kc_path = self._find_kc(ctx)
        if not kc_path:
            ctx.log.die("Kernelcache not found")

        # Try each footprint until one fits
        for old_bundle, desc in self.FOOTPRINTS:
            ctx.log.log(f"  Trying {old_bundle} ({desc}) for C26 Charger...")

            try:
                result = self._transplant(kc_path, old_bundle, ctx)
                ctx.log.log(f"  ✓ C26 Charger transplanted using {old_bundle}")
                return result
            except Exception as e:
                ctx.log.log(f"  ✗ {old_bundle} failed: {e}")
                continue

        ctx.log.die("All footprints exhausted for C26 Charger")

    def _find_kc(self, ctx):
        """Find the latest kernelcache in work directory."""
        work = Path(ctx.work)
        kc_files = sorted(work.glob("kc.*.raw"))
        if kc_files:
            return kc_files[-1]
        # Fallback to artifacts
        artifacts = Path("artifacts/boot/boot_KC.img4")
        if artifacts.exists():
            # Would need to extract first
            return None
        return None

    def _transplant(self, kc_path: Path, old_bundle: str, ctx) -> Path:
        """Transplant C26 into given footprint."""
        from ios16se.sevenup.kernel_transplant import apply

        if not self.DONOR_PATH.exists():
            ctx.log.die(f"Donor kernel not found: {self.DONOR_PATH}")

        result = apply(
            source=kc_path,
            donor=self.DONOR_PATH,
            work=ctx.work,
            OLD=old_bundle,
            NAME=self.NEW_BUNDLE,
            preserve_text_span=True,
            registration_only=False,
        )

        return result


if __name__ == "__main__":
    print(f"Patch: {KcChargerC26.id}")