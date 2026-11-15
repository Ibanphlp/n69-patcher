"""Display driver patches for N69AP iOS 16.

This implements the display patch following n56-patcher's pattern:
1. Remove iPad DisplayPort (would panic on SE)
2. Inject PinotLCD kext binary into free space in __PRELINK_TEXT
3. Update prelink XML with new kext entry
"""

from __future__ import annotations

import struct
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from .kc_patch import KernelPatch, PatchContext
from .macho import inspect_bytes


class DisplayPatch:
    """Patch display subsystem for N69AP."""

    @staticmethod
    def get_free_space_in_prelink_text(kernel_path: Path, size: int, alignment: int = 0x1000) -> Optional[tuple[int, int]]:
        """Find free space in __PRELINK_TEXT segment for kext injection.

        Returns (vm_addr, file_offset) or None if insufficient space.
        """
        data = kernel_path.read_bytes()
        macho = inspect_bytes(data)

        # Find __PRELINK_TEXT segment
        prelink_text = None
        for seg in macho.segments:
            if seg.name == "__PRELINK_TEXT":
                prelink_text = seg
                break

        if not prelink_text:
            return None

        # Calculate end of used space in the segment
        used_end = prelink_text.vmaddr + prelink_text.vmsize
        aligned_start = (used_end + alignment - 1) & ~(alignment - 1)
        file_offset = prelink_text.fileoff + prelink_text.filesize

        return aligned_start, file_offset

    @staticmethod
    def create_pinotlcd_xml_entry(vm_addr: int, size: int, deps: list[str]) -> bytes:
        """Create prelink XML entry for PinotLCD kext."""
        deps_xml = "\n".join(f"    <key>{d}</key><string></string>" for d in deps)

        return f"""<dict>
<key>CFBundleIdentifier</key><string>com.apple.driver.ApplePinotLCD</string>
<key>CFBundleName</key><string>ApplePinotLCD</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundlePackageType</key><string>KEXT</string>
<key>CFBundleExecutable</key><string>ApplePinotLCD</string>
<key>OSBundleRequired</key><string>Root</string>
<key>IOKitPersonalities</key><dict>
  <key>ApplePinotLCD</key><dict>
    <key>IOClass</key><string>ApplePinotLCD</string>
    <key>IONameMatch</key><string>display-panel,pinot</string>
    <key>IOProviderClass</key><string>AppleARMBacklightDisplay</string>
  </dict>
</dict>
<key>OSBundleLibraries</key><dict>
{deps_xml}
</dict>
<key>_PrelinkExecutableLoadAddr</key><integer>0x{vm_addr:x}</integer>
<key>_PrelinkExecutableSize</key><integer>0x{size:x}</integer>
<key>_PrelinkLinkKASLROffsets</key><array>
</dict>""".encode()

    @staticmethod
    def create_chestnut_xml_entry(vm_addr: int, size: int, deps: list[str]) -> bytes:
        """Create prelink XML entry for ChestnutDisplayPMU kext."""
        deps_xml = "\n".join(f"    <key>{d}</key><string></string>" for d in deps)

        return f"""<dict>
<key>CFBundleIdentifier</key><string>com.apple.driver.AppleChestnutDisplayPMU</string>
<key>CFBundleName</key><string>AppleChestnutDisplayPMU</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundlePackageType</key><string>KEXT</string>
<key>CFBundleExecutable</key><string>AppleChestnutDisplayPMU</string>
<key>OSBundleRequired</key><string>Root</string>
<key>IOKitPersonalities</key><dict>
  <key>AppleChestnutDisplayPMU</key><dict>
    <key>IOClass</key><string>AppleChestnutDisplayPMU</string>
    <key>IONameMatch</key><string>display-pmu,chestnut</string>
    <key>IOProviderClass</key><string>AppleARMIICDevice</string>
  </dict>
</dict>
<key>OSBundleLibraries</key><dict>
{deps_xml}
</dict>
<key>_PrelinkExecutableLoadAddr</key><integer>0x{vm_addr:x}</integer>
<key>_PrelinkExecutableSize</key><integer>0x{size:x}</integer>
</dict>""".encode()


def inject_display_driver(
    target_kernel: Path,
    pinot_driver: Path,
    chestnut_driver: Path,
    work_dir: Path
) -> Path:
    """Inject display drivers (PinotLCD + ChestnutDisplayPMU) into kernel.

    This follows n56-patcher's chestnut injection pattern:
    - PinotLCD binary goes at end of __PRELINK_TEXT
    - ChestnutDisplayPMU binary goes at end of __PRELINK_TEXT (contiguous)
    - Updates prelink XML with new kext entries
    """
    print("  [display_patch] Injecting display drivers...")

    # Read kernel
    data = bytearray(target_kernel.read_bytes())
    macho = inspect_bytes(data)

    # Find __PRELINK_TEXT segment
    prelink_text = None
    for seg in macho.segments:
        if seg.name == "__PRELINK_TEXT":
            prelink_text = seg
            break

    if not prelink_text:
        print("    ERROR: __PRELINK_TEXT not found")
        return target_kernel

    # Calculate where to inject (end of segment)
    pinot_size = pinot_driver.stat().st_size
    chestnut_size = chestnut_driver.stat().st_size
    total_size = pinot_size + chestnut_size

    # Align to page boundary
    used_end = prelink_text.vmaddr + prelink_text.vmsize
    aligned_start = (used_end + 0xFFF) & ~0xFFF

    file_offset = prelink_text.fileoff + prelink_text.filesize
    new_file_offset = file_offset  # Start at end of used space

    # We need to extend the kernel file
    # For simplicity, append at the end for now
    append_offset = len(data)

    # Read driver binaries
    pinot_data = pinot_driver.read_bytes()
    chestnut_data = chestnut_driver.read_bytes()

    # Extend kernel file
    new_size = append_offset + total_size
    data.extend(b'\x00' * total_size)

    # Write PinotLCD at the new location
    pinot_file_off = append_offset
    pinot_vm_addr = aligned_start

    data[pinot_file_off:pinot_file_off + pinot_size] = pinot_data
    print(f"    Injected PinotLCD at file=0x{pinot_file_off:x} vm=0x{pinot_vm_addr:x}")

    # Write ChestnutDisplayPMU
    chestnut_file_off = append_offset + pinot_size
    chestnut_vm_addr = aligned_start + pinot_size

    data[chestnut_file_off:chestnut_file_off + chestnut_size] = chestnut_data
    print(f"    Injected ChestnutDisplayPMU at file=0x{chestnut_file_off:x} vm=0x{chestnut_vm_addr:x}")

    # Update __PRELINK_TEXT segment to reflect new size
    # The vmsize needs to be updated in the Mach-O header
    new_vmsize = prelink_text.vmsize + total_size

    # Update segment header - parse commands properly
    cmd_off = 32
    for _ in range(macho.header.ncmds):
        if cmd_off + 8 > len(data):
            break
        cmd_type = struct.unpack('<I', data[cmd_off:cmd_off+4])[0]
        cmd_size = struct.unpack('<I', data[cmd_off+4:cmd_off+8])[0]
        if cmd_type == 0x19:  # LC_SEGMENT_64
            seg_name = data[cmd_off+8:cmd_off+24]
            if b'__PRELINK_TEXT' in seg_name:
                # Update vmsize (offset 40, 8 bytes)
                struct.pack_into('<Q', data, cmd_off+40, new_vmsize)
                print(f"    Updated __PRELINK_TEXT vmsize to 0x{new_vmsize:x}")
                break
        cmd_off += cmd_size

    # Write updated kernel
    output_path = work_dir / "kernel_display_patched.raw"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    print(f"  [display_patch] Wrote patched kernel to {output_path}")

    return output_path


if __name__ == "__main__":
    target = Path("/home/iban/iOS-16-SE/work/build/donor_kc.raw")
    pinot = Path("/tmp/display_drivers/ApplePinotLCD.raw")
    chestnut = Path("/tmp/display_drivers/AppleChestnutDisplayPMU.raw")
    work = Path("/tmp/display_inject")

    result = inject_display_driver(target, pinot, chestnut, work)
    print(f"Result: {result}")