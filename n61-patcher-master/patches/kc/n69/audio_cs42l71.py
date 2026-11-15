#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CS42L71 audio codec + CS35L19 amplifier driver transplants.

Audio stack for N69AP:
1. Replace CS42L81 with CS42L71 codec
2. Add CS35L19 amplifier (from AppleAE2Audio footprint)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src" / "ios16se" / "sevenup"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from core.patch import Patch
from ios16se.sevenup.kernel_transplant import apply


class KcAudioCs42l71(Patch):
    id = "kc.audio_cs42l71"
    layer = "kc"
    role = "transform"
    feature = "audio_cs42l71"
    scope = "device"
    requires = "kc.remove_ipad_drivers"
    note = "CS42L71 音频编解码器 (替换 CS42L81)"

    OLD_BUNDLE = "com.apple.driver.AppleCS42L81Audio"
    NEW_BUNDLE = "com.apple.driver.AppleCS42L71Audio"

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
            ctx.log.log(f"    Success: CS42L71 codec")

            # Also need amp
            amp_result = self._transplant_amp(result, ctx)
            return amp_result
        except Exception as e:
            ctx.log.log(f"    [warn] {e}")
            return kc_path

    def _transplant_amp(self, kc_path: Path, ctx) -> Path:
        """Transplant CS35L19 amplifier."""
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")

        try:
            ctx.log.log(f"  [{self.id}] Transplanting AppleCS35L19Amp...")
            result = apply(
                source=kc_path,
                donor=se_donor,
                work=ctx.work,
                OLD="com.apple.driver.AppleAE2Audio",
                NAME="com.apple.driver.AppleCS35L19Amp",
                preserve_text_span=True,
                registration_only=False,
            )
            ctx.log.log(f"    Success: CS35L19 amplifier")
            return result
        except Exception as e:
            ctx.log.log(f"    [warn] Amp transplant: {e}")
            return kc_path


class KcAudioCs35l19(Patch):
    id = "kc.audio_cs35l19"
    layer = "kc"
    role = "transform"
    feature = "audio_cs35l19"
    scope = "device"
    requires = "kc.audio_cs42l71"
    note = "CS35L19 放大器驱动"

    def apply(self, ctx, art=None):
        # Already done in parent patch
        kc_path = ctx._find_latest_kernelcache()
        return kc_path if kc_path else ctx.work / "kc.dummy"


if __name__ == "__main__":
    print(f"Patch: {KcAudioCs42l71.id}, {KcAudioCs35l19.id}")