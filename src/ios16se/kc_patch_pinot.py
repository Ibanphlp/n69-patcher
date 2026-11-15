"""PinotLCD display driver patch for N69AP.

Uses SevenUp's transplant engine to replace AppleDPDisplay footprint with ApplePinotLCD.
"""
from pathlib import Path

from .kc_patch import KernelPatch, PatchContext
from .sevenup.kernel_transplant import apply


class DisplayPinotPatch(KernelPatch):
    """Port Pinot/MIPI-DSIM display driver into the freed AppleDPDisplay footprint."""

    name = "display_pinot"
    description = "Replace iPad DPDisplay with N69AP Pinot MIPI-DSIM"
    requires = ["remove_ipad_drivers"]

    def __init__(self, donor_kc: Path = None):
        self.donor_kc = donor_kc

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Display patch - transplanting PinotLCD into AppleDPDisplay footprint")

        donor = self.donor_kc or ctx.donor_kc
        if not donor or not donor.exists():
            print(f"  [{self.name}] WARNING: Donor kernel not found, skipping display transplant.")
            return ctx.kernel_path

        try:
            result = apply(
                source=ctx.kernel_path,
                donor=donor,
                work=ctx.work_dir,
                OLD="com.apple.driver.AppleDPDisplay",
                NAME="com.apple.driver.ApplePinotLCD",
                SOURCE_MARKER=b"PinotLCD",
                TARGET_MARKER=b"DPDisplay",
                preserve_text_span=True,
                registration_only=False,
            )
            print(f"  [{self.name}] Successfully transplanted PinotLCD")
            return result
        except Exception as exc:
            print(f"  [{self.name}] Transplant skipped ({exc}); keeping kernel path.")
            return ctx.kernel_path

