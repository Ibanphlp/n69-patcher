"""N69 driver transplants. One implementation, called by apply_patches.

Print-only stubs are gone. Each boot-path patch calls the SevenUP engine
and fails closed when the output is unchanged, the bundle is missing, or a
relocation still points at the donor.
"""

from __future__ import annotations

from pathlib import Path

from .driver_transplant import (
    CHESTNUT, DISPLAY_FOOTPRINT, PINOT, PMU_DONOR, PMU_FOOTPRINT, SPI,
)
from .driver_transplant import (
    replace_personality, transplant_chestnut, transplant_footprint,
)
from .kc_patch import KernelPatch, PatchContext
from .sep_shim import SepShimError, apply_sep, prologue_was_replaced_with_ret


def _book(ctx: PatchContext):
    book = ctx.config.get("bindings")
    if book is None:
        raise RuntimeError(f"{ctx.kernel_path}: SE/iPad binding book was not created")
    return book


def _donor(ctx: PatchContext, patch: KernelPatch) -> Path:
    donor = getattr(patch, "donor_kc", None) or ctx.donor_kc
    if donor is None or not Path(donor).is_file():
        raise RuntimeError(f"{patch.name}: SE 15.8.8 kernel is required")
    if Path(donor).resolve() == Path(ctx.kernel_path).resolve():
        raise RuntimeError(f"{patch.name}: donor kernel is the iPad kernel")
    return Path(donor)


class PmuD2255Patch(KernelPatch):
    name = "pmu_d2255"
    description = "Place AppleD2255PMU in the D2365 allocations with reviewed bindings"
    requires = ["generic_security"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        # DialogPMU is bound before the D2255 personality is installed: the
        # binding book resolves DialogPMU references as part of bind_bundle,
        # and SevenUP only then writes the D2255 IOKit personalities.
        return transplant_footprint(
            ctx.kernel_path, _donor(ctx, self), ctx.work_dir,
            PMU_FOOTPRINT, PMU_DONOR, _book(ctx),
        )


class ChestnutPatch(KernelPatch):
    name = "chestnut"
    description = "Transplant AppleChestnutDisplayPMU into empty prelink space"
    requires = ["pmu_d2255"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        return transplant_chestnut(ctx.kernel_path, _donor(ctx, self), ctx.work_dir, _book(ctx))


class DisplayPinotPatch(KernelPatch):
    name = "display_pinot"
    description = "Replace AppleDPDisplay with ApplePinotLCD and rebind AppleARMPlatform"
    requires = ["chestnut"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        # AppleDPDisplay is the footprint. Leftover DisplayPort personalities
        # are removed only by remove_ipad_drivers, after this link exists.
        return transplant_footprint(
            ctx.kernel_path, _donor(ctx, self), ctx.work_dir,
            DISPLAY_FOOTPRINT, PINOT, _book(ctx),
        )


class TouchSpin69Patch(KernelPatch):
    name = "touch_spin69"
    description = "Transplant AppleMultitouchSPI, then link the SPIN69 personality"
    requires = ["display_pinot"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        donor = _donor(ctx, self)
        linked = transplant_footprint(
            ctx.kernel_path, donor, ctx.work_dir, SPI, SPI, _book(ctx),
        )
        ctx.kernel_path = linked
        return replace_personality(linked, donor, ctx.work_dir)


class SepLegacyPatch(KernelPatch):
    name = "sep_legacy"
    description = "Rewrite the iOS 16 SEP request builder to the v2 layout"
    requires = ["pmu_d2255"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        output = ctx.work_dir / "kernel_sep_legacy.raw"
        try:
            report = apply_sep(ctx.kernel_path, output)
        except SepShimError as error:
            raise RuntimeError(f"sep_legacy: {error}") from error
        if prologue_was_replaced_with_ret(ctx.kernel_path.read_bytes(), output.read_bytes()):
            raise RuntimeError("sep_legacy replaced a function prologue with ret")
        ctx.config["sep_report"] = report
        return output


class AudioCS42L71Patch(KernelPatch):
    """Present but not on the boot path until its own binding test passes."""

    name = "audio_cs42l71"
    description = "Transplant AppleCS42L71Audio once every import binds"
    requires = []

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        # The CS42L81 allocation cannot hold this codec. Stop before transplantation.
        raise RuntimeError(
            "audio_cs42l71 is not on the boot path; AppleCS42L81Audio is too small "
            "and the CS42L71 binding set is not yet a boot dependency"
        )


class AudioCS35L19Patch(KernelPatch):
    name = "audio_cs35l19"
    description = "SE amplifier, withheld until AppleEmbeddedAudio bindings pass"
    requires = ["audio_cs42l71"]

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        raise RuntimeError(
            "audio_cs35l19 is withheld; no personality for a missing amplifier is installed"
        )


class ChargerC26Patch(KernelPatch):
    name = "charger_c26"
    description = "AppleC26Charger needs a footprint larger than AppleHDQGasGaugeControl"
    requires = []

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        raise RuntimeError(
            "charger_c26: AppleHDQGasGaugeControl is too small (Image does not fit __TEXT); "
            "C26 is not truncated and is not on the boot path"
        )


class WifiBcmPatch(KernelPatch):
    name = "wifi_bcm"
    description = "Rewrite hash-store records to the stock SE firmware hashes"
    requires = []

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        from .wifi_hash import WifiHashError, apply_wifi
        directory = Path(ctx.config.get("wifi_firmware", "artifacts/n69-wifi"))
        try:
            return apply_wifi(ctx, directory)
        except WifiHashError as error:
            raise RuntimeError(f"wifi_bcm: {error}") from error


class VibrationDrv2604Patch(KernelPatch):
    name = "vibration_drv2604"
    description = "DRV2604 stays disabled unless the iPad class is the same driver"
    requires = []

    def __init__(self, donor_kc: Path | None = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        raise RuntimeError(
            "vibration_drv2604 is disabled; the iPad kernel has no matching DRV2604 "
            "class to edit, and no personality is left probing a missing driver"
        )
