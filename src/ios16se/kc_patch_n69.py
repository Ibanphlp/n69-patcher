"""Compatibility imports.

The print-only N69 stubs used to live here and a second copy lived in
``kc_patch_drivers``. ``apply_patches`` calls ``kc_patch_drivers`` only.
"""

from .kc_patch_drivers import (
    AudioCS35L19Patch,
    AudioCS42L71Patch,
    ChargerC26Patch,
    DisplayPinotPatch,
    PmuD2255Patch,
    SepLegacyPatch,
    TouchSpin69Patch,
    VibrationDrv2604Patch,
    WifiBcmPatch,
)

__all__ = [
    "AudioCS35L19Patch",
    "AudioCS42L71Patch",
    "ChargerC26Patch",
    "DisplayPinotPatch",
    "PmuD2255Patch",
    "SepLegacyPatch",
    "TouchSpin69Patch",
    "VibrationDrv2604Patch",
    "WifiBcmPatch",
]
