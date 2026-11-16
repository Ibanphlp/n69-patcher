"""KernelCache patching framework - inspired by n56-patcher's patch system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import subprocess
import shutil
import re
import struct

@dataclass
class PatchContext:
    """Context passed to all patches during application."""
    kernel_path: Path
    work_dir: Path
    im4m: Optional[Path] = None
    config: dict = field(default_factory=dict)
    applied_patches: list[str] = field(default_factory=list)
    kernel_macho_base: int = 0xfffffff000000000  # Base VM address of kernel Mach-O
    donor_kc: Optional[Path] = None

class KernelPatch(ABC):
    """Base class for a kernelcache patch."""
    name: str = ""
    description: str = ""
    requires: list[str] = []
    after: list[str] = []

    @abstractmethod
    def apply(self, ctx: PatchContext) -> Path:
        """Apply patch, return new kernel path."""
        pass

    def can_apply(self, ctx: PatchContext) -> bool:
        """Check if patch can be applied (dependencies met)."""
        for req in self.requires:
            if req not in ctx.applied_patches:
                return False
        return True

class PatchRegistry:
    """Registry of all available kernel patches."""
    def __init__(self):
        self._patches: dict[str, KernelPatch] = {}

    def register(self, patch: KernelPatch):
        self._patches[patch.name] = patch

    def get(self, name: str) -> Optional[KernelPatch]:
        return self._patches.get(name)

class RemoveIpadDriversPatch(KernelPatch):
    """Remove iPad-only drivers from __PRELINK_INFO safely.

    Inlines every IDREF globally first (so removing an entry can never
    orphan a reference into it), then removes the target entries.
    """
    name = "remove_ipad_drivers"
    description = "Remove iPad-specific drivers that panic on N69AP"

    IPAD_DRIVERS = [
        "AppleMultitouchSPIC1F15",
        "AppleANXDPTX",
        "AppleParadeDP805",
        "AppleParadeDP825",
        "AppleDPDisplayBacklightController",
        "AppleAgileDPDisplay",
        "AppleDPDisplayPowerController",
        "IODisplayPortFamily",
    ]

    def apply(self, ctx: PatchContext) -> Path:
        from .sevenup.kernel_transplant import entry_range

        data = bytearray(ctx.kernel_path.read_bytes())
        prelink_info = self._find_prelink_info(data)
        if prelink_info is None:
            raise ValueError("Kernel has no __PRELINK_INFO segment")
        fileoff, filesize = prelink_info

        xml = bytes(data[fileoff:fileoff + filesize]).rstrip(b'\0')
        # Expand only references to definitions in the entries being removed.
        # Inlining every IDREF makes the fixed-size prelink segment overflow.
        from .driver_transplant import LEFTOVER_DP, protected_footprints
        protected = protected_footprints(ctx.applied_patches)
        ranges = []
        identifiers = re.findall(rb'<key>CFBundleIdentifier</key><string>([^<]+)</string>', xml)
        present = {name.decode() for name in identifiers}
        for bundle in LEFTOVER_DP:
            if bundle in protected:
                raise RuntimeError(f"remove_ipad_drivers would delete transplant footprint {bundle}")
            if bundle not in present:
                continue
            ranges.append(entry_range(xml, bundle))
            print(f"    Removing iPad driver entry: {bundle}")
        if not ranges:
            raise RuntimeError("remove_ipad_drivers found no leftover DisplayPort personalities")
        xml = self._remove_entries(xml, ranges)

        if len(xml) > filesize:
            raise ValueError(f"Prelink XML grew to {len(xml)} > capacity {filesize}")

        data[fileoff:fileoff + filesize] = xml.ljust(filesize, b'\0')
        output = ctx.work_dir / f"kernel_{self.name}.raw"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        return output

    @staticmethod
    def _remove_entries(xml: bytes, ranges: list[tuple[int, int]]) -> bytes:
        """Remove entries while preserving typed IDREF definitions they own."""
        import copy
        import xml.etree.ElementTree as ET
        from .sevenup.kc import plist

        root = ET.fromstring(xml)
        defs = {e.attrib['ID']: e for e in root.iter() if 'ID' in e.attrib}
        removed = set()
        for start, end in ranges:
            removed.update(x.decode() for x in re.findall(rb'\bID="([^"]+)"', xml[start:end]))
        result = xml
        for start, end in sorted(ranges, reverse=True):
            result = result[:start] + result[end:]

        def clone_definition(ident, stack=()):
            if ident in stack or ident not in defs:
                raise ValueError(f"Invalid or cyclic prelink IDREF: {ident}")
            element = copy.deepcopy(defs[ident])
            def clean(e):
                ref = e.attrib.get('IDREF')
                if ref in removed:
                    return clone_definition(ref, (*stack, ident))
                e.attrib.pop('ID', None)
                for i, child in enumerate(e):
                    e[i] = clean(child)
                return e
            return clean(element)

        for ident in sorted(removed):
            pattern = rb'<[A-Za-z][A-Za-z0-9_]*\b[^>]*\bIDREF="' + re.escape(ident.encode()) + rb'"[^>]*/>'
            if re.search(pattern, result):
                replacement = ET.tostring(clone_definition(ident), encoding='utf-8')
                result = re.sub(pattern, lambda _m: replacement, result)
        remaining = set(re.findall(rb'\bIDREF="([^"]+)"', result))
        present = set(re.findall(rb'\bID="([^"]+)"', result))
        if remaining - present:
            raise ValueError("Dangling prelink IDREF after driver removal")
        # Exercise the same typed parser used by the kernel analysis pipeline.
        plist(result)
        return result

    def _find_prelink_info(self, data: bytes) -> tuple[int, int] | None:
        for i in range(len(data) - 72):
            if data[i:i+4] == b'\x19\x00\x00\x00':
                seg_name = data[i+8:i+24]
                if b'__PRELINK_INFO' in seg_name:
                    vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', data, i+24)
                    return fileoff, filesize
        return None

from .kc_patch_drivers import (
    AudioCS35L19Patch, AudioCS42L71Patch, ChargerC26Patch, ChestnutPatch,
    DisplayPinotPatch, PmuD2255Patch, SepLegacyPatch, TouchSpin69Patch,
    VibrationDrv2604Patch, WifiBcmPatch,
)


class GenericSecurityPatch(KernelPatch):
    name = "generic_security"
    description = "Apply generic iOS 16 kernel security patches"
    # Kernel64Patcher3 flags for an iOS 16 arm64 (A9) kernel. Each was verified to
    # locate its target in the iPad 16.7.16 (20H392) kernel:
    #   -a AMFI                  -f AppleFirmwareUpdate img4 check
    #   -w image4 validation cb  -h root hash authentication (iOS 16)
    #   -e root volume seal broken   -d developer mode
    FLAGS = ["-a", "-f", "-w", "-h", "-e", "-d"]
    # Log line each flag prints when it really patched something.
    EXPECT = {
        "-a": "Patching AMFI at",
        "-f": "Patching \"%s::%s() Performing img4 validation",
        "-w": "[+] Patched Img4DecodeGetPropertyData",
        "-h": "Patching is_root_hash_authentication_required_ios at",
        "-e": "Patched tbnz at",
        "-d": "Patching developer mode at",
    }

    @staticmethod
    def _find_tool() -> Path:
        root = Path(__file__).resolve().parents[2]
        for cand in (root / "SevenUP-main/Darwin/Kernel64Patcher3",
                     root / "ramdisk-main/Darwin/Kernel64Patcher3"):
            if cand.exists():
                return cand
        raise RuntimeError(
            "Kernel64Patcher3 not found (SevenUP-main/Darwin/Kernel64Patcher3). "
            "Without AMFI/root-hash/seal patches the iOS 16 kernel cannot boot.")

    def apply(self, ctx: PatchContext) -> Path:
        tool = self._find_tool()
        out = ctx.work_dir / "kernel_generic_security.raw"
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([str(tool), str(ctx.kernel_path), str(out), *self.FLAGS],
                           capture_output=True, text=True)
        log = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Kernel64Patcher3 failed (rc={r.returncode}):\n{log[-800:]}")
        missing = [f for f, text in self.EXPECT.items() if text not in log]
        if missing:
            raise RuntimeError(
                f"generic_security: patcher did not find targets for {missing}; "
                f"refusing to continue with an unpatched kernel.\n{log[-800:]}")
        if out.stat().st_size != ctx.kernel_path.stat().st_size:
            raise RuntimeError("generic_security: patched kernel changed size")
        print(f"    [generic_security] applied {' '.join(self.FLAGS)}")
        return out

class WatchtowerPatch(KernelPatch):
    name = "watchtower"
    description = "Append watchtower tail"
    requires = ["remove_ipad_drivers"]
    def __init__(self, donor_kc): self.donor_kc = donor_kc
    def apply(self, ctx: PatchContext) -> Path:
        # TODO: call append_wt_fix.py
        return ctx.kernel_path

class ComplzssRepackPatch(KernelPatch):
    name = "complzss_repack"
    description = "Recompress"
    requires = ["watchtower"]
    def apply(self, ctx: PatchContext) -> Path:
        # TODO: call mkcomplzss
        return ctx.kernel_path

# safe-minimal is the PMU plus the reviewed SEP shim. full adds Chestnut,
# Pinot and SPIN69. minimal is only the AMFI/img4/root-hash/seal/developer
# patches. Audio, charger, Wi-Fi and haptics stay out of every boot mode.
BOOT_MODES = {
    "minimal": ("generic_security",),
    "safe-minimal": ("generic_security", "pmu_d2255", "sep_legacy"),
    "full": ("generic_security", "pmu_d2255", "sep_legacy", "chestnut",
             "display_pinot", "touch_spin69", "remove_ipad_drivers"),
}


def patches_for_mode(mode: str, enabled: dict | None = None) -> list[str]:
    """Return the ordered patch names for one boot mode.

    ``enabled`` is the profile table. A mode never gains a patch the profile
    turned off, and it never gains audio, charger, Wi-Fi or haptics.
    """

    if mode not in BOOT_MODES:
        raise RuntimeError(f"Unsupported kernel mode: {mode}")
    selected = list(BOOT_MODES[mode])
    if enabled is not None:
        selected = [name for name in selected if enabled.get(name, {}).get("enabled", True)]
    blocked = {"audio_cs42l71", "audio_cs35l19", "charger_c26", "wifi_bcm", "vibration_drv2604"}
    if blocked.intersection(selected):
        raise RuntimeError("audio, charger, Wi-Fi and haptics are not on the boot path")
    return selected


def apply_patches(kernel_path, patch_names, work_dir, im4m=None, config=None, donor_kc=None) -> Path:
    registry = PatchRegistry()
    se_kernel = donor_kc
    if config and config.get("se_kernel"):
        se_kernel = Path(config["se_kernel"])
    patches = [
        RemoveIpadDriversPatch(), GenericSecurityPatch(),
        PmuD2255Patch(se_kernel),
        ChestnutPatch(se_kernel),
        DisplayPinotPatch(se_kernel),
        TouchSpin69Patch(se_kernel),
        AudioCS42L71Patch(se_kernel),
        AudioCS35L19Patch(se_kernel),
        ChargerC26Patch(se_kernel),
        SepLegacyPatch(se_kernel),
        WifiBcmPatch(se_kernel),
        VibrationDrv2604Patch(se_kernel),
        WatchtowerPatch(donor_kc),
        ComplzssRepackPatch(),
    ]
    for patch in patches:
        registry.register(patch)
    ordered = ["generic_security", "pmu_d2255", "chestnut",
               "display_pinot", "touch_spin69", "sep_legacy", "remove_ipad_drivers",
               "audio_cs42l71", "audio_cs35l19", "charger_c26", "wifi_bcm",
               "vibration_drv2604", "watchtower", "complzss_repack"]
    ordered = [name for name in ordered if name in patch_names]
    # A required driver that is absent from the output is not a boot kernel.
    required_markers = {
        "pmu_d2255": b"com.apple.driver.AppleD2255PMU",
        "chestnut": b"com.apple.driver.AppleChestnutDisplayPMU",
        "display_pinot": b"com.apple.driver.ApplePinotLCD",
        "touch_spin69": b"com.apple.driver.AppleMultitouchSPI",
    }
    ctx = PatchContext(kernel_path, work_dir, im4m, config or {}, donor_kc=se_kernel)
    work_dir.mkdir(parents=True, exist_ok=True)
    if se_kernel and Path(se_kernel).is_file() and Path(se_kernel) != Path(kernel_path):
        from .driver_transplant import BindingBook
        ctx.config["bindings"] = BindingBook(Path(se_kernel), Path(kernel_path))
    current = kernel_path
    for name in ordered:
        patch = registry.get(name)
        if patch is None:
            raise RuntimeError(f"unknown kernel patch {name}")
        if not patch.can_apply(ctx):
            raise RuntimeError(f"{name} requirements are not met: {patch.requires}")
        produced = patch.apply(ctx)
        if produced is None or not Path(produced).is_file():
            raise RuntimeError(f"{name} did not write a kernel")
        if name not in {"watchtower", "complzss_repack"} and Path(produced).read_bytes() == Path(current).read_bytes():
            raise RuntimeError(f"{name} left the kernel byte-identical")
        marker = required_markers.get(name)
        if marker is not None and marker not in Path(produced).read_bytes():
            raise RuntimeError(f"{name} finished without {marker.decode()} in the kernel")
        current = produced
        ctx.kernel_path = current
        ctx.applied_patches.append(name)
    return current
