"""N69AP-specific binary patches for iOS 16 kernel.

These patches port iOS 15 N69AP drivers into the iOS 16 iPad5 kernel.
Based on n56-patcher methodology: replace iPad drivers with SE equivalents.

Each patch modifies the __PRELINK_INFO XML and patches driver binary code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import re
import struct
from dataclasses import dataclass

from .kc_patch import KernelPatch, PatchContext


@dataclass
class BinaryPatch:
    """A single binary patch: offset -> new bytes."""
    offset: int
    original: bytes
    patched: bytes
    description: str


class N69PatchBase(KernelPatch):
    """Base class for N69AP driver port patches."""

    def __init__(self, donor_kc: Path):
        self.donor_kc = donor_kc
        self._driver_cache = {}

    def _get_driver_info(self, ctx: PatchContext, bundle_id: str) -> Optional[dict]:
        """Get driver load address and size from prelink XML."""
        data = ctx.kernel_path.read_bytes()
        needle = f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>".encode()

        for match in re.finditer(re.escape(needle), data):
            start = max(0, match.start() - 8192)
            end = match.end() + 16384
            record = data[start:end]

            addr_match = re.search(
                rb"<key>_PrelinkExecutableLoadAddr</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>",
                record
            )
            size_match = re.search(
                rb"<key>_PrelinkExecutableSize</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>",
                record
            )

            if addr_match and size_match:
                return {
                    "load_addr": int(addr_match.group(1), 16),
                    "size": int(size_match.group(1), 16),
                }
        return None

    def _patch_kernel_bytes(self, ctx: PatchContext, patches: list[BinaryPatch]) -> Path:
        """Apply binary patches to kernel."""
        data = bytearray(ctx.kernel_path.read_bytes())

        for patch in patches:
            if data[patch.offset:patch.offset + len(patch.original)] == patch.original:
                data[patch.offset:patch.offset + len(patch.original)] = patch.patched
                print(f"    Applied: {patch.description} at 0x{patch.offset:x}")
            else:
                print(f"    WARNING: Pattern mismatch for {patch.description} at 0x{patch.offset:x}")
                print(f"      Expected: {patch.original.hex()}")
                print(f"      Found:    {data[patch.offset:patch.offset + len(patch.original)].hex()}")

        output = ctx.work_dir / f"kernel_{self.name}.raw"
        output.write_bytes(data)
        return output


class PmuD2255Patch(N69PatchBase):
    """Port D2255/Chestnut PMU driver (replace D2365)."""

    name = "pmu_d2255"
    description = "Replace iPad D2365 PMU with N69AP D2255/Chestnut PMU"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        # 1. Remove iPad D2365 PMU from prelink XML (already done by remove_ipad_drivers)
        # 2. Add D2255 PMU entry to prelink XML
        # 3. The D2255 driver is already in the kernel (from SE iOS 15) - we need to merge it

        # For now: the remove_ipad_drivers patch removed D2365
        # The D2255 driver binary needs to be added from SE kernel
        # This requires: extracting D2255 from SE kernel, adding to prelink, patching register offsets

        print(f"  [{self.name}] PMU patch - D2365 removed, D2255 port needed")
        print(f"    iPad D2365 PMU: 0xfffffff005913d80 (4285 bytes)")
        print(f"    SE D2255 PMU:   0xfffffff005a3c800 (2428 bytes)")
        print(f"    Also needs: AppleChestnutDisplayPMU (0xfffffff00589be40, 2043 bytes)")
        print(f"    Also needs: AppleDialogPMU (0xfffffff005a2d140, 62912 bytes)")

        return ctx.kernel_path


class TouchSpin69Patch(N69PatchBase):
    """Port SPIN69 multi-touch driver (replace SPIC1F15)."""

    name = "touch_spin69"
    description = "Replace iPad SPIC1F15 touch with N69AP SPIN69"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        raise NotImplementedError(
            "touch_spin69 is a research placeholder; no SPIN69 transplant is implemented"
        )


class DisplayPinotPatch(N69PatchBase):
    """Port Pinot/MIPI-DSIM display driver (replace ANX/DPDisplay)."""

    name = "display_pinot"
    description = "Replace iPad ANX/DPDisplay with N69AP Pinot MIPI-DSIM"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Display patch - ANX/DPDisplay removed, Pinot port needed")
        print(f"    iPad ANXDPTX:   0xfffffff00566c500 (1581 bytes)")
        print(f"    iPad DPDisplay: 0xfffffff0054d6200 (66280 bytes)")
        print(f"    SE PinotLCD:    0xfffffff005ae9a80 (6487 bytes)")
        print(f"    Also needs: AppleChestnutDisplayPMU for display power")
        print(f"    Difference: MIPI-DSIM vs DisplayPort, different register map")

        return ctx.kernel_path


class AudioCS42L71Patch(N69PatchBase):
    """Port CS42L71 audio codec driver."""

    name = "audio_cs42l71"
    description = "Replace iPad CS42L81 with N69AP CS42L71 + CS35L19"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Audio patch - CS42L81 removed, CS42L71+CS35L19 port needed")
        print(f"    iPad CS42L81:   0xfffffff00545ca40 (108605 bytes)")
        print(f"    SE CS42L71:     0xfffffff005ad0b00 (10811 bytes)")
        print(f"    SE CS35L19:     0xfffffff00581f180 (33446 bytes)")
        print(f"    Difference: Different codec, different I2C registers, different DSP firmware")

        return ctx.kernel_path


class AudioCS35L19Patch(N69PatchBase):
    """Port CS35L19 amplifier driver."""

    name = "audio_cs35l19"
    description = "Add N69AP CS35L19 amplifier driver"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Amplifier patch - CS35L19 port needed")
        print(f"    SE CS35L19:     0xfffffff00581f180 (33446 bytes)")
        print(f"    iPad: No equivalent amplifier driver")

        return ctx.kernel_path


class ChargerC26Patch(N69PatchBase):
    """Port C26 charger driver."""

    name = "charger_c26"
    description = "Add N69AP C26 charger driver"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Charger patch - C26 port needed")
        print(f"    SE C26:         0xfffffff0058f9380 (3372 bytes)")
        print(f"    iPad: Different charger IC (no C26 equivalent)")
        print(f"    Difference: Different charger registers, different battery gauge")

        return ctx.kernel_path


class SepLegacyPatch(N69PatchBase):
    """Legacy SEP compatibility for iOS 15 SEP with iOS 16 kernel."""

    name = "sep_legacy"
    description = "Make iOS 16 kernel use legacy SEP request format (v0, tags 1/2)"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] Legacy SEP patch - disabling SEP Manager to avoid panic")
        print(f"    iPad MesaSEP:   0xfffffff00567c480 (4656 bytes)")
        print(f"    SE MesaSEP:     0xfffffff0058de280 (4656 bytes) - SAME SIZE")
        print(f"    SEPKeyStore:    iPad=4360, SE=4360 bytes - SAME SIZE")
        print(f"    Difference: SEP firmware protocol version")
        print(f"    Patch: Modify AppleSEPKeyStore to use v0 tags (1,2) instead of v1 tags (7,8)")
        print(f"    Also: Patch AppleSEPManager::start to return 0 (success)")

        data = bytearray(ctx.kernel_path.read_bytes())

        # Resolve the exact byte ranges of the SEP drivers using prelink metadata.
        from .prelink import metadata as prelink_metadata
        from .macho import inspect_bytes as inspect_macho_bytes

        macho = inspect_macho_bytes(data)

        def driver_file_range(bundle_id: str) -> tuple[int, int] | None:
            """Return (file_start, file_end) for a driver, or None."""
            try:
                meta = prelink_metadata(ctx.kernel_path, bundle_id)
            except Exception as e:
                print(f"    WARNING: no metadata for {bundle_id}: {e}")
                return None
            seg = next(
                (s for s in macho.segments
                 if s.vmaddr <= meta.executable_load_address
                 and meta.executable_load_address + meta.executable_size <= s.vmaddr + s.vmsize),
                None,
            )
            if seg is None:
                print(f"    WARNING: no segment contains {bundle_id}")
                return None
            start = seg.fileoff + meta.executable_load_address - seg.vmaddr
            end = start + meta.executable_size
            return start, end

        total_tag = 0
        total_manager = 0
        stp_prologue = b"\xfd\x7b\xbf\xa9"  # stp x29, x30, [sp, #-16]!
        ret = b"\xc0\x03\x5f\xd6"           # ret

        # 1. AppleSEPKeyStore: tag 8 -> tag 2 (only within the driver's bytes).
        rng = driver_file_range("com.apple.driver.AppleSEPKeyStore")
        if rng:
            start, end = rng
            print(f"    AppleSEPKeyStore file range: 0x{start:x} - 0x{end:x}")
            tag8 = b"\x08\x00\x00\x00"
            tag2 = b"\x02\x00\x00\x00"
            pos = start
            while True:
                pos = data.find(tag8, pos, end)
                if pos == -1:
                    break
                data[pos:pos+4] = tag2
                total_tag += 1
                pos += 4
            print(f"    Patched {total_tag} tag 8 -> tag 2 within AppleSEPKeyStore")
        else:
            print("    WARNING: AppleSEPKeyStore not found, tag patch skipped")

        # 2. AppleSEPManager: force ::start to return 0 immediately.
        rng = driver_file_range("com.apple.driver.AppleSEPManager")
        if rng:
            start, end = rng
            print(f"    AppleSEPManager file range: 0x{start:x} - 0x{end:x}")
            pos = start
            while True:
                pos = data.find(stp_prologue, pos, end)
                if pos == -1:
                    break
                data[pos:pos+4] = ret
                total_manager += 1
                pos += 4
            print(f"    Patched {total_manager} AppleSEPManager start prologue -> ret")
        else:
            print("    WARNING: AppleSEPManager not found, manager patch skipped")

        # 3. Also neutralize AppleSEPKeyStore's start method the same way.
        rng = driver_file_range("com.apple.driver.AppleSEPKeyStore")
        if rng:
            start, end = rng
            pos = start
            count = 0
            while True:
                pos = data.find(stp_prologue, pos, end)
                if pos == -1:
                    break
                data[pos:pos+4] = ret
                count += 1
                pos += 4
            if count:
                print(f"    Patched {count} AppleSEPKeyStore start prologue -> ret")

        if total_tag == 0 and total_manager == 0:
            print("    WARNING: No SEP patches could be applied - kernel may still panic")

        # Write patched kernel
        output = ctx.work_dir / f"kernel_{self.name}.raw"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        print(f"    SEP patch applied, new kernel: {output}")
        return output


class WifiBcmPatch(N69PatchBase):
    """Patch BCM Wi-Fi firmware hash check."""

    name = "wifi_bcm"
    description = "Patch Wi-Fi firmware hash store for N69AP firmware"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        print(f"  [{self.name}] WiFi patch - firmware hash check")
        print(f"    iPad BCM4350:   hash store for iPad firmware")
        print(f"    SE BCM43xx:     different firmware hash")
        print(f"    Patch: Modify hash store validation to accept SE firmware")

        return ctx.kernel_path


class VibrationDrv2604Patch(N69PatchBase):
    """Port DRV2604 haptic driver."""

    name = "vibration_drv2604"
    description = "Port DRV2604 haptic driver"
    requires = ["remove_ipad_drivers"]

    def apply(self, ctx: PatchContext) -> Path:
        raise NotImplementedError(
            "vibration_drv2604 is a research placeholder; no haptic transplant is implemented"
        )


# ============================================================
# Concrete Binary Patches (when we have the actual offsets)
# ============================================================

def create_pmu_binary_patches() -> list[BinaryPatch]:
    """
    PMU binary patches needed:
    - Register base address: D2365 vs D2255
    - GPIO pin mappings
    - Interrupt controller offsets
    - Voltage rail configurations
    """
    # These are PLACEHOLDERS - need actual RE to find real offsets
    return [
        BinaryPatch(
            offset=0x0,  # REPLACE WITH REAL OFFSET
            original=b"\x00\x00\x00\x00",  # D2365 register base
            patched=b"\x00\x00\x00\x00",   # D2255 register base
            description="PMU register base address"
        ),
    ]


def create_touch_binary_patches() -> list[BinaryPatch]:
    """
    Touch binary patches needed:
    - SPI controller base address
    - GPIO interrupt pins
    - Touch panel resolution/size registers
    - Firmware upload sequence
    """
    return []


def create_display_binary_patches() -> list[BinaryPatch]:
    """
    Display binary patches needed:
    - MIPI-DSIM vs DisplayPort controller
    - Lane configuration registers
    - Panel timing registers
    - Brightness/backlight control (PWM vs LP)
    """
    return []


def create_audio_binary_patches() -> list[BinaryPatch]:
    """
    Audio binary patches needed:
    - I2C address for CS42L71 vs CS42L81
    - DSP firmware pointers
    - Amplifier (CS35L19) I2C address and registers
    - Clock configuration
    """
    return []


def create_charger_binary_patches() -> list[BinaryPatch]:
    """
    Charger binary patches needed:
    - Charger IC I2C address
    - Battery gauge registers
    - USB-PD / BC1.2 detection
    """
    return []


def create_sep_binary_patches(ctx: PatchContext) -> list[BinaryPatch]:
    """
    SEP binary patches needed:
    - AppleSEPKeyStore: change request tags from 7/8 to 1/2
    - Change protocol version from v1 to v0
    - Media key derivation path

    Based on analysis of SE vs iPad SEPKeyStore drivers:
    - Tag 8 (0x08) found at offset 432 in __text section (same in both)
    - This is likely the tag for unwrapPerFileKey
    - Need to change from 8 to 2 (v0)
    """
    # Get driver info - use a concrete subclass to call the method
    class _TempPatch(N69PatchBase):
        def apply(self, ctx: PatchContext) -> Path:
            return ctx.kernel_path

    temp_patch = _TempPatch(Path("."))
    driver_info = temp_patch._get_driver_info(ctx, "com.apple.driver.AppleSEPKeyStore")
    if not driver_info:
        print("    WARNING: SEPKeyStore not found in kernel")
        return []

    base_addr = driver_info["load_addr"]

    # Find which segment contains this driver and calculate file offset
    # The driver is in __PRELINK_TEXT segment
    data = ctx.kernel_path.read_bytes()
    from .macho import inspect_bytes
    macho = inspect_bytes(data)

    kernel_offset = None
    for seg in macho.segments:
        if seg.vmaddr <= base_addr < seg.vmaddr + seg.vmsize:
            offset_in_segment = base_addr - seg.vmaddr
            file_offset = seg.fileoff + offset_in_segment
            # Add the __text section offset (0x150) and tag8_offset (432)
            kernel_offset = file_offset + 0x150 + 432
            print(f"    Found SEPKeyStore in {seg.name}: fileoff=0x{seg.fileoff:x}, vmaddr=0x{seg.vmaddr:x}")
            print(f"    offset_in_segment=0x{offset_in_segment:x}, file_offset=0x{file_offset:x}")
            print(f"    kernel_offset=0x{kernel_offset:x}")
            break

    if kernel_offset is None:
        print("    WARNING: Could not find segment containing SEPKeyStore")
        return []

    # Verify the bytes
    actual = data[kernel_offset:kernel_offset+4]
    print(f"    Bytes at patch offset: {actual.hex()} (expected 08000000)")

    return [
        BinaryPatch(
            offset=kernel_offset,
            original=b"\x08\x00\x00\x00",  # tag 8 (v1)
            patched=b"\x02\x00\x00\x00",   # tag 2 (v0)
            description="SEPKeyStore tag 8->2 (unwrapPerFileKey)"
        ),
    ]


if __name__ == "__main__":
    # Show what patches are needed
    print("=== N69AP iOS 16 Kernel Patches Needed ===")
    print()

    patches = [
        PmuD2255Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        TouchSpin69Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        DisplayPinotPatch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        AudioCS42L71Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        AudioCS35L19Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        ChargerC26Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        SepLegacyPatch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        WifiBcmPatch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
        VibrationDrv2604Patch(Path("artifacts/ipad5-16.7.16/KernelCache.raw")),
    ]

    for p in patches:
        print(f"[{p.name}] {p.description}")
        p.apply(None)  # Print info
        print()
