#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPIN69 multi-touch driver transplant.

Replaces iPad SPIC1F15 touch footprint with N69AP SPIN69 driver.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src" / "ios16se" / "sevenup"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from core.patch import Patch
from ios16se.sevenup.kernel_transplant import apply


class KcTouchSpin69(Patch):
    id = "kc.touch_spin69"
    layer = "kc"
    role = "transform"
    feature = "touch_spin69"
    scope = "device"
    requires = "kc.remove_ipad_drivers"
    note = "SPIN69 触摸驱动 (替换 SPIC1F15)"

    OLD_BUNDLE = "com.apple.driver.AppleMultitouchSPIC1F15"
    NEW_BUNDLE = "com.apple.driver.SPIN69"

    def apply(self, ctx, art=None):
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if not se_donor.exists():
            ctx.log.die(f"Donor kernel not found: {se_donor}")

        kc_path = ctx._find_latest_kernelcache()
        if not kc_path:
            ctx.log.die("No kernelcache found")

        ctx.log.log(f"  [{self.id}] Transplanting {self.NEW_BUNDLE}...")

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
            ctx.log.log(f"    Success: SPIN69 touch driver")
            return result
        except Exception as e:
            ctx.log.log(f"    [warn] {e}")
            return kc_path


if __name__ == "__main__":
    patch = KcTouchSpin69()
    print(f"Patch: {patch.id}")