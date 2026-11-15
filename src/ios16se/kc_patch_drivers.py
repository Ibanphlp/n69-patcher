"""N69AP Full Driver Transplantation Engine for iOS 16 (SevenUp Methodology).

Transplants:
1. PinotLCD Display -> AppleDPDisplay footprint
2. CS42L71 Audio -> AppleCS42L81Audio footprint
3. D2255 PMU -> AppleD2365PMU footprint
4. DRV2604 Vibration / Haptics -> iPad Haptics footprint
"""

from pathlib import Path
from typing import Optional

from .kc_patch import KernelPatch, PatchContext
from .sevenup.kernel_transplant import apply
from .macho import MachOError, inspect as inspect_macho
from .prelink import PrelinkError, inspect as inspect_prelink


class TransplantedDriverPatch(KernelPatch):
    """Generic driver patch using SevenUp's footprint-displacement engine."""

    old_bundle: str = ""
    new_bundle: str = ""

    def __init__(self, donor_kc: Optional[Path] = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Transplanting {self.new_bundle} into {self.old_bundle} footprint...")

        donor = self.donor_kc or getattr(ctx, "donor_kc", None)
        if not donor or not donor.is_file():
            raise RuntimeError(f"{self.name}: donor kernel not found: {donor}")

        result = apply(
            source=ctx.kernel_path,
            donor=donor,
            work=ctx.work_dir,
            OLD=self.old_bundle,
            NAME=self.new_bundle,
            preserve_text_span=True,
            registration_only=False,
        )
        if result == ctx.kernel_path or not result.is_file():
            raise RuntimeError(f"{self.name}: transplant did not produce a new kernel")
        try:
            inspect_macho(result)
            bundles = inspect_prelink(result).bundle_identifiers
        except (MachOError, PrelinkError) as exc:
            raise RuntimeError(f"{self.name}: output kernel failed structural validation: {exc}") from exc
        if self.new_bundle not in bundles:
            raise RuntimeError(f"{self.name}: output is missing transplanted bundle {self.new_bundle}")
        if result.read_bytes() == ctx.kernel_path.read_bytes():
            raise RuntimeError(f"{self.name}: transplant left the kernel unchanged")
        print(f"  [{self.name}] Successfully transplanted and validated {self.new_bundle}")
        return result


class DisplayPinotPatch(TransplantedDriverPatch):
    name = "display_pinot"
    description = "Replace iPad DPDisplay with N69AP Pinot MIPI-DSIM"
    requires = ["remove_ipad_drivers"]
    old_bundle = "com.apple.driver.AppleDPDisplay"
    new_bundle = "com.apple.driver.ApplePinotLCD"

    def apply(self, ctx: PatchContext) -> Path:
        # Use N69AP iOS 15 kernel as donor for N69AP-specific drivers
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if se_donor.exists():
            self.donor_kc = se_donor
        return super().apply(ctx)


class AudioCS42L71Patch(TransplantedDriverPatch):
    name = "audio_cs42l71"
    description = "Replace iPad Audio with N69AP CS42L71"
    requires = ["remove_ipad_drivers"]
    # CS42L81 footprint is too small (5KB); use AppleANXDPTX (54KB) instead.
    # AppleANXDPTX is removed by RemoveIpadDriversPatch so it's safe to reclaim.
    old_bundle = "com.apple.driver.AppleANXDPTX"
    new_bundle = "com.apple.driver.AppleCS42L71Audio"

    def apply(self, ctx: PatchContext) -> Path:
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if se_donor.exists():
            self.donor_kc = se_donor

        return super().apply(ctx)


class AudioCS35L19Patch(TransplantedDriverPatch):
    name = "audio_cs35l19"
    description = "Add N69AP CS35L19 amplifier driver"
    requires = ["audio_cs42l71"]
    old_bundle = "com.apple.driver.AppleAE2Audio"  # Use iPad AE2 as footprint
    new_bundle = "com.apple.driver.AppleCS35L19Amp"


class PmuD2255Patch(TransplantedDriverPatch):
    name = "pmu_d2255"
    description = "Replace iPad D2365 PMU with N69AP D2255 PMU"
    requires = ["remove_ipad_drivers"]
    old_bundle = "com.apple.driver.AppleD2365PMU"
    new_bundle = "com.apple.driver.AppleD2255PMU"

    def apply(self, ctx: PatchContext) -> Path:
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if se_donor.exists():
            self.donor_kc = se_donor
        return super().apply(ctx)


class WifiBcmPatch(KernelPatch):
    """Patch AppleBCMWLANCore hash store for SE BCM43xx Wi-Fi chip."""

    name = "wifi_bcm"
    description = "Patch Wi-Fi firmware hash check for SE BCM chip"
    requires = ["remove_ipad_drivers"]

    def __init__(self, donor_kc: Optional[Path] = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Patching Wi-Fi firmware hash store validation...")
        data = bytearray(ctx.kernel_path.read_bytes())
        ctx.work_dir.mkdir(parents=True, exist_ok=True)

        # Wi-Fi firmware hash validation bypass pattern for AppleBCMWLANCore
        # Replaces exact hash comparison return value with success (1)
        pattern = b"\x00\x00\x80\x52\xc0\x03\x5f\xd6"  # mov w0, #0 ; ret
        replacement = b"\x20\x00\x80\x52\xc0\x03\x5f\xd6"  # mov w0, #1 ; ret

        idx = data.find(pattern)
        if idx != -1 and data.find(pattern, idx + 1) == -1:
            data[idx:idx+len(replacement)] = replacement
            print(f"    Patched Wi-Fi hash check at offset 0x{idx:x}")
            out_path = ctx.work_dir / "kernel_wifi_patched.raw"
            out_path.write_bytes(data)
            return out_path
        elif idx == -1:
            raise RuntimeError("wifi_bcm: expected hash check pattern was not found")
        else:
            raise RuntimeError("wifi_bcm: hash check pattern is ambiguous; refusing a blind patch")


class ChargerC26Patch(TransplantedDriverPatch):
    """Port C26 charger driver from SE iOS 15 kernel into iPad5 iOS 16 kernel.

    Uses footprint-displacement engine to transplant com.apple.driver.AppleC26Charger.
    The C26 charger is 10769 bytes in SE 15.8.8 kernel.
    """
    name = "charger_c26"
    description = "Add N69AP C26 charger driver (transplant from SE 15.8.8)"
    requires = ["remove_ipad_drivers"]
    old_bundle = "com.apple.driver.AppleHDQGasGaugeControl"  # 8708 bytes footprint
    new_bundle = "com.apple.driver.AppleC26Charger"

    def apply(self, ctx: PatchContext) -> Path:
        se_donor = Path("artifacts/n69ap-15.8.8/KernelCache.raw")
        if se_donor.exists():
            self.donor_kc = se_donor
        return super().apply(ctx)
