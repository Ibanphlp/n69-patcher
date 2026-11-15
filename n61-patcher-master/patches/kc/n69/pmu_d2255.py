#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2255/Chestnut PMU driver transplant.

Replaces iPad D2365 PMU footprint with N69AP D2255 PMU + DialogPMU + ChestnutDisplayPMU.

Uses SevenUP's footprint-displacement engine via Python.
"""
from pathlib import Path
import sys

# Add our Python transplant engine
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src" / "ios16se" / "sevenup"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from core.patch import Patch
from ios16se.sevenup.kernel_transplant import apply


class KcPmuD2255(Patch):
    id = "kc.pmu_d2255"
    layer = "kc"
    role = "transform"
    feature = "pmu_d2255"
    scope = "device"
    requires = "kc.remove_ipad_drivers"
    note = "D2255/Chestnut PMU 移植 (替换 D2365)"

    # Footprint: iPad D2365 PMU (com.apple.driver.AppleD2365PMU)
    # New driver: N69AP D2255 PMU (com.apple.driver.AppleD2255PMU)
    # Also need: AppleDialogPMU, AppleChestnutDisplayPMU
    OLD_BUNDLE = "com.apple.driver.AppleD2365PMU"
    NEW_BUNDLE = "com.apple.driver.AppleD2255PMU"

    def apply(self, ctx, art=None):
        # Donor: N69AP iOS 15.8.8 kernel (has D2255, Dialog, ChestnutDisplay PMU)
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if not se_donor.exists():
            ctx.log.die(f"Donor kernel not found: {se_donor}")

        # Source: current patched kernel (iPad5 16.7.16 base)
        kc_path = ctx.work / "kc.generic_security.raw"
        if not kc_path.exists():
            # Find latest kc.*.raw
            kc_files = sorted(ctx.work.glob("kc.*.raw"))
            if not kc_files:
                ctx.log.die("No kernelcache found in work dir")
            kc_path = kc_files[-1]

        ctx.log.log(f"  [{self.id}] Transplanting {self.NEW_BUNDLE} into {self.OLD_BUNDLE} footprint...")

        try:
            result = apply(
                source=kc_path,
                donor=se_donor,
                work=ctx.work,
                OLD=self.OLD_BUNDLE,
                NAME=self.NEW_BUNDLE,
                preserve_text_span=True,
                registration_only=False,
            )
            ctx.log.log(f"  [{self.id}] Successfully transplanted PMU")

            # Also transplant AppleDialogPMU (same footprint pattern)
            # and AppleChestnutDisplayPMU
            result = self._transplant_additional(result, ctx)

        except Exception as exc:
            ctx.log.die(f"  [{self.id}] Transplant failed: {exc}")

        return result

    def _transplant_additional(self, kc_path: Path, ctx) -> Path:
        """Transplant additional PMU-related drivers."""
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")

        additional = [
            ("com.apple.driver.AppleDialogPMU", "com.apple.driver.AppleDialogPMU"),
            ("com.apple.driver.AppleChestnutDisplayPMU", "com.apple.driver.AppleChestnutDisplayPMU"),
        ]

        current = kc_path
        for old, new in additional:
            try:
                ctx.log.log(f"  [{self.id}] Transplanting {new}...")
                current = apply(
                    source=current,
                    donor=se_donor,
                    work=ctx.work,
                    OLD=old,
                    NAME=new,
                    preserve_text_span=True,
                    registration_only=False,
                )
                ctx.log.log(f"    Success: {new}")
            except Exception as e:
                ctx.log.log(f"    [warn] {new}: {e}")

        return current


if __name__ == "__main__":
    patch = KcPmuD2255()
    print(f"Patch: {patch.id}")