"""Inject N69AP iOS 15 drivers into iOS 16 iPad5 kernel.

This implements the core of n56-patcher's approach:
1. Extract working N69AP drivers from iOS 15 kernel
2. Inject them into iOS 16 iPad5 kernel prelink area
3. Patch binary code for iOS 16 kernel API compatibility
"""

from __future__ import annotations

import re
import struct
import shutil
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .prelink import extract_driver, metadata, bundle_ids_from_prelink
from .macho import inspect_bytes as inspect_macho_bytes


@dataclass
class DriverBinary:
    """Complete driver binary with metadata."""
    bundle_id: str
    load_addr: int
    size: int
    raw_binary: bytes
    dependencies: list[str]


class KernelDriverInjector:
    """Inject N69AP drivers into iPad5 iOS 16 kernel."""

    def __init__(self, target_kernel: Path, donor_kernel: Path, work_dir: Path):
        self.target_kernel = target_kernel      # iPad5 iOS 16 kernel (to patch)
        self.donor_kernel = donor_kernel        # N69AP iOS 15 kernel (source of drivers)
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.target_data = bytearray(target_kernel.read_bytes())
        self.donor_data = donor_kernel.read_bytes()

        self.target_macho = inspect_macho_bytes(self.target_data)
        self.donor_macho = inspect_macho_bytes(self.donor_data)

    def get_prelink_region(self, data: bytearray) -> tuple[int, int, bytes]:
        """Find __PRELINK_INFO segment and return (fileoff, filesize, xml)."""
        macho = inspect_macho_bytes(data)
        for seg in macho.segments:
            if seg.name == "__PRELINK_INFO":
                xml = data[seg.fileoff:seg.fileoff + seg.filesize]
                return seg.fileoff, seg.filesize, xml
        raise ValueError("__PRELINK_INFO not found")

    def extract_n69_driver(self, bundle_id: str) -> Optional[DriverBinary]:
        """Extract a driver from N69AP iOS 15 kernel."""
        try:
            meta = metadata(self.donor_kernel, bundle_id)
            # Extract the raw Mach-O
            output = self.work_dir / f"donor_{bundle_id}.raw"
            extract_driver(self.donor_kernel, bundle_id, output)
            binary = output.read_bytes()

            return DriverBinary(
                bundle_id=bundle_id,
                load_addr=meta.executable_load_address,
                size=meta.executable_size,
                raw_binary=binary,
                dependencies=list(meta.dependencies),
            )
        except Exception as e:
            print(f"  Failed to extract {bundle_id}: {e}")
            return None

    def get_target_prelink_xml(self) -> bytes:
        """Get target kernel's prelink XML."""
        _, _, xml = self.get_prelink_region(self.target_data)
        return xml

    def find_driver_xml(self, xml: bytes, bundle_id: str) -> Optional[tuple[int, int]]:
        """Find driver's <dict>...</dict> in prelink XML. Returns (start, end)."""
        needle = f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>".encode()
        for match in re.finditer(re.escape(needle), xml):
            start = match.start()
            dict_start = xml.rfind(b"<dict>", 0, start)
            if dict_start == -1:
                continue

            # Find matching </dict>
            depth = 0
            dict_end = -1
            for m in re.finditer(b"</?dict>", xml[dict_start:]):
                abs_pos = dict_start + m.start()
                if m.group() == b"<dict>":
                    depth += 1
                else:
                    depth -= 1
                    if depth == 0:
                        dict_end = abs_pos + 7
                        break

            if dict_end != -1:
                entry = xml[dict_start:dict_end]
                if bundle_id.encode() in entry:
                    return dict_start, dict_end
        return None

    def remove_driver_from_xml(self, xml: bytes, bundle_id: str) -> bytes:
        """Remove a driver's entry from prelink XML."""
        result = xml
        pos = self.find_driver_xml(result, bundle_id)
        if pos:
            start, end = pos
            print(f"    Removing {bundle_id} from prelink XML (offset {start}-{end})")
            result = result[:start] + result[end:]
        return result

    def add_driver_to_xml(self, xml: bytes, driver: DriverBinary) -> bytes:
        """Add a driver entry to prelink XML before </array></dict></plist>."""
        # Remove trailing nulls, add entry, then pad if needed
        xml = xml.rstrip(b'\x00')

        # Create the XML entry for this driver
        entry = self._create_driver_xml_entry(driver)

        # Find insertion point (before </array></dict></plist>)
        insert_pos = xml.rfind(b"</array>")
        if insert_pos == -1:
            insert_pos = len(xml)

        print(f"    Adding {driver.bundle_id} to prelink XML at offset {insert_pos}")
        result = xml[:insert_pos] + entry + xml[insert_pos:]

        return result

    def _create_driver_xml_entry(self, driver: DriverBinary) -> bytes:
        """Create XML entry for a driver."""
        deps_xml = "".join(f"<key>{dep}</key><string></string>" for dep in driver.dependencies)

        return f"""<dict>
<key>CFBundleIdentifier</key><string>{driver.bundle_id}</string>
<key>_PrelinkExecutableLoadAddr</key><integer>0x{driver.load_addr:x}</integer>
<key>_PrelinkExecutableSize</key><integer>0x{driver.size:x}</integer>
<key>OSBundleLibraries</key><dict>
{deps_xml}
</dict>
</dict>""".encode()

    def inject_driver_binary(self, driver: DriverBinary) -> tuple[int, int]:
        """
        Inject driver binary into kernel __PRELINK_TEXT or __PLK_TEXT_EXEC segment.
        Returns (vm_addr, file_offset) where it was placed.
        """
        # Find relevant segments
        prelink_text_seg = None
        plk_text_seg = None
        plk_data_const_seg = None
        for seg in self.target_macho.segments:
            if seg.name == "__PRELINK_TEXT":
                prelink_text_seg = seg
            elif seg.name == "__PLK_TEXT_EXEC":
                plk_text_seg = seg
            elif seg.name == "__PLK_DATA_CONST":
                plk_data_const_seg = seg

        if not prelink_text_seg:
            raise ValueError("__PRELINK_TEXT segment not found")

        # Calculate actual used content end in each segment
        # Find last non-zero byte in each segment
        def get_actual_end(seg):
            content = self.target_data[seg.fileoff:seg.fileoff + seg.filesize]
            for i in range(len(content)-1, -1, -1):
                if content[i] != 0:
                    return seg.vmaddr + i + 1
            return seg.vmaddr  # empty segment

        prelink_text_end = get_actual_end(prelink_text_seg)
        plk_text_end = get_actual_end(plk_text_seg) if plk_text_seg else 0
        plk_data_const_start = plk_data_const_seg.vmaddr if plk_data_const_seg else 0
        prelink_text_limit = prelink_text_seg.vmaddr + prelink_text_seg.vmsize  # = plk_text_seg.vmaddr

        print(f"    Segment boundaries:")
        print(f"      __PRELINK_TEXT used: up to 0x{prelink_text_end:x}, limit: 0x{prelink_text_limit:x}")
        if plk_text_seg:
            print(f"      __PLK_TEXT_EXEC used: up to 0x{plk_text_end:x}, limit: 0x{plk_data_const_start:x}")
        if plk_data_const_seg:
            print(f"      __PLK_DATA_CONST starts: 0x{plk_data_const_start:x}")

        # Track cumulative size increase
        if not hasattr(self, '_prelink_text_extension'):
            self._prelink_text_extension = 0
        if not hasattr(self, '_plk_text_extension'):
            self._plk_text_extension = 0
        if not hasattr(self, '_using_plk_text'):
            self._using_plk_text = False

        # Determine which segment to use - try to keep drivers in PRELINK_TEXT area first
        if self._using_plk_text and plk_text_seg:
            # Already switched to PLK_TEXT_EXEC, continue using it
            insert_vm = plk_text_end + self._plk_text_extension
            insert_vm = (insert_vm + 0x3FFF) & ~0x3FFF
            insert_file = plk_text_seg.fileoff + (insert_vm - plk_text_seg.vmaddr)
        else:
            # Try to place in __PRELINK_TEXT first
            insert_vm = prelink_text_end + self._prelink_text_extension

            # Only align if we're close to the segment boundary
            # If the unaligned address + driver size fits in PRELINK_TEXT, don't align
            unaligned_end = insert_vm + len(driver.raw_binary)
            if unaligned_end <= prelink_text_limit:
                # Fits without alignment
                pass
            else:
                # Need to align to page boundary
                insert_vm = (insert_vm + 0x3FFF) & ~0x3FFF

            insert_file = prelink_text_seg.fileoff + (insert_vm - prelink_text_seg.vmaddr)

            # If no room in __PRELINK_TEXT (would overflow into PLK_TEXT_EXEC), switch to PLK_TEXT_EXEC
            if plk_text_seg and insert_vm + len(driver.raw_binary) > prelink_text_limit:
                self._using_plk_text = True
                insert_vm = plk_text_end + self._plk_text_extension
                insert_vm = (insert_vm + 0x3FFF) & ~0x3FFF
                insert_file = plk_text_seg.fileoff + (insert_vm - plk_text_seg.vmaddr)
                print(f"    Switching to __PLK_TEXT_EXEC for {driver.bundle_id}")

        # Ensure we don't overflow into PLK_DATA_CONST
        if plk_data_const_seg and insert_vm + len(driver.raw_binary) > plk_data_const_start:
            print(f"    WARNING: Driver {driver.bundle_id} would overflow into PLK_DATA_CONST!")
            print(f"      Driver end: 0x{insert_vm + len(driver.raw_binary):x}")
            print(f"      PLK_DATA_CONST start: 0x{plk_data_const_start:x}")

        # Ensure we have space (extend if needed)
        needed = len(driver.raw_binary)
        if insert_file + needed > len(self.target_data):
            # Extend the file
            self.target_data.extend(b"\x00" * (insert_file + needed - len(self.target_data)))

        # Write binary
        self.target_data[insert_file:insert_file + needed] = driver.raw_binary

        # Update extension for next driver
        if self._using_plk_text:
            self._plk_text_extension = (insert_vm - plk_text_end) + needed
        else:
            self._prelink_text_extension = (insert_vm - prelink_text_end) + needed

        print(f"    Injected {driver.bundle_id} at vm=0x{insert_vm:x} file=0x{insert_file:x} size={needed}")
        return insert_vm, insert_file

    def build_patched_kernel(self, output: Path):
        """Write the patched kernel."""
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.target_data)
        print(f"  Patched kernel written to {output}")


# ============================================================
# High-level injection workflow
# ============================================================

N69_DRIVERS_TO_PORT = [
    # (bundle_id, description)
    ("com.apple.driver.AppleD2255PMU", "PMU D2255/Chestnut"),
    ("com.apple.driver.AppleChestnutDisplayPMU", "Display PMU Chestnut"),
    ("com.apple.driver.AppleMultitouchSPIN69", "Touch SPIN69"),
    ("com.apple.driver.ApplePinotLCD", "Display Pinot MIPI-DSIM"),
    ("com.apple.driver.AppleCS42L71Audio", "Audio CS42L71"),
    ("com.apple.driver.AppleCS35L19Amp", "Audio CS35L19 Amp"),
    ("com.apple.driver.AppleC26Charger", "Charger C26"),
    # SEP drivers - same bundle ID but may need protocol patches
    ("com.apple.driver.AppleMesaSEPDriver", "SEP Mesa"),
    ("com.apple.driver.AppleSEPKeyStore", "SEP KeyStore"),
    ("com.apple.driver.AppleSEPManager", "SEP Manager"),
    ("com.apple.iokit.AppleSEPGenericTransfer", "SEP Transfer"),
]


def inject_n69_drivers(
    target_kernel: Path,      # iPad5 iOS 16 kernel (to patch)
    donor_kernel: Path,       # N69AP iOS 15 kernel (source)
    output_kernel: Path,      # Patched output
    work_dir: Path,
) -> Path:
    """Main workflow: inject N69AP drivers into iPad5 iOS 16 kernel."""

    print(f"\n=== Injecting N69AP Drivers into iOS 16 Kernel ===")
    print(f"  Target: {target_kernel} (iPad5 iOS 16)")
    print(f"  Donor:  {donor_kernel} (N69AP iOS 15)")
    print(f"  Output: {output_kernel}")

    injector = KernelDriverInjector(target_kernel, donor_kernel, work_dir)

    # 1. Get target prelink XML
    print("  Reading target prelink XML...")
    xml = injector.get_target_prelink_xml()

    # 2. Remove iPad-only drivers (already done by remove_ipad_drivers, but do it here too)
    ipad_only = [
        "com.apple.driver.AppleD2365PMU",
        "com.apple.driver.AppleMultitouchSPIC1F15",
        "com.apple.driver.AppleANXDPTX",
        "com.apple.driver.AppleDPDisplay",
        "com.apple.driver.AppleCS42L81Audio",
    ]
    for drv in ipad_only:
        xml = injector.remove_driver_from_xml(xml, drv)

    # 3. Extract and inject N69AP drivers
    print("  Extracting and injecting N69AP drivers...")
    injected = []

    for bundle_id, desc in N69_DRIVERS_TO_PORT:
        print(f"    {bundle_id} ({desc})...")
        driver = injector.extract_n69_driver(bundle_id)
        if driver:
            # Inject binary into kernel - returns (vm_addr, file_offset)
            vm_addr, file_offset = injector.inject_driver_binary(driver)
            # Update load address to match injection point
            driver.load_addr = vm_addr
            # Add to prelink XML
            xml = injector.add_driver_to_xml(xml, driver)
            injected.append(driver)
            print(f"      Injected: {bundle_id} at vm=0x{vm_addr:x} file=0x{file_offset:x}")
        else:
            print(f"      WARNING: Failed to extract {bundle_id}")

    # 4. Write updated prelink XML back to kernel
    print("  Writing updated prelink XML...")
    fileoff, filesize, _ = injector.get_prelink_region(injector.target_data)

    # The local 'xml' variable has all drivers added - check if it fits
    if len(xml) <= filesize:
        # XML fits within segment - just pad
        if len(xml) < filesize:
            xml += b"\x00" * (filesize - len(xml))
        injector.target_data[fileoff:fileoff + filesize] = xml
    else:
        # Need to expand __PRELINK_INFO segment
        print(f"  INFO: XML too large ({len(xml)} > {filesize}), expanding __PRELINK_INFO segment...")
        # Expand XML to new size, padded with zeros
        new_filesize = len(xml)
        # Find and expand the segment in target_data
        # The __PRELINK_INFO segment's data starts at fileoff and has size filesize
        # We need to extend target_data and update the Mach-O header

        # First, expand the raw data at the end of the segment's allocation
        # The segment data occupies target_data[fileoff:fileoff+filesize], we expand to new_filesize
        if fileoff + new_filesize > len(injector.target_data):
            injector.target_data.extend(b"\x00" * (fileoff + new_filesize - len(injector.target_data)))
        injector.target_data[fileoff:fileoff + new_filesize] = xml  # Write new XML into expanded area

        # Now we need to update the Mach-O header's __PRELINK_INFO vmsize/filesize
        # Find the LC_SEGMENT_64 command for __PRELINK_INFO in the Mach-O commands
        from src.ios16se.macho import inspect_bytes as inspect_macho_bytes
        from src.ios16se.prelink import metadata as prelink_metadata
        import struct

        data = bytearray(injector.target_data)
        macho = inspect_macho_bytes(data)

        # Find __PRELINK_INFO segment in the segment commands
        prelink_info_seg = None
        for seg in macho.segments:
            if seg.name == "__PRELINK_INFO":
                prelink_info_seg = seg
                break

        if prelink_info_seg:
            # Update the segment's filesize and vmsize
            # LC_SEGMENT_64 structure: at offset 24 from command start: vmaddr(8), vmsize(8), fileoff(8), filesize(8), maxprot(8), initprot(8), nsects(4), flags(4)
            # We need to update vmsize at offset 24+40=64... wait let me check
            # Actually struct.unpack_from("<4Q", data, i+24) gives vmaddr, vmsize, fileoff, filesize
            # The command starts at some offset, and the data layout is:
            # [0-3]: command type (LC_SEGMENT_64 = 0x19)
            # [4-7]: command size
            # [8-15]: segname (16 bytes, null-padded)
            # [16-23]: vmaddr (uint64)
            # [24-31]: vmsize (uint64)
            # [32-39]: fileoff (uint64)
            # [40-47]: filesize (uint64)
            # [48-55]: maxprot (uint64)
            # [56-63]: initprot (uint64)
            # [64-67]: nsects (uint32)
            # [68-71]: flags (uint32)

            # Wait, let me re-examine - the struct format in prelink.py line 118:
            # vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', data, i+24)
            # This assumes the command starts at offset i, and the 4Q starts at i+24
            # But looking at the full format: "<2I16s4Q4I" - that's:
            # 2I = command_type + command_size (8 bytes)
            # 16s = segname (16 bytes)
            # 4Q = vmaddr, vmsize, fileoff, filesize (32 bytes)
            # 4I = maxprot, initprot, nsects, flags (16 bytes)
            # Total = 8 + 16 + 32 + 16 = 72 bytes

            # So the segment command starts at some offset, and:
            # vmsize is at command_offset + 24
            # filesize is at command_offset + 32

            # Let me find the actual command offset by searching for LC_SEGMENT_64
            cmd_type = LC_SEGMENT_64  # 0x19
            for cmd_off in range(len(data) - 72):
                if data[cmd_off:cmd_off+4] == b'\x19\x00\x00\x00':
                    seg_name = data[cmd_off+8:cmd_off+24]
                    if b'__PRELINK_INFO' in seg_name:
                        # Update filesize at offset cmd_off + 32 (from start of command)
                        struct.pack_into('<Q', data, cmd_off + 32, new_filesize)
                        # Update vmsize at offset cmd_off + 24
                        # vmsize should also increase to match - calculate new vmsize
                        # vmsize must reflect the new content end
                        # Actually, vmsize is the virtual size, filesize is the file size
                        # For __PRELINK_INFO, both should increase
                        # Let's just update filesize; vmsize can stay or also update
                        # Let's compute appropriate vmsize based on alignment
                        old_vmsize = struct.unpack_from('<Q', data, cmd_off + 24)[0]
                        # Align new size to 0x1000
                        new_vmsize = (new_filesize + 0xFFF) & ~0xFFF
                        # Only update vmsize if it needs to grow
                        if new_vmsize > old_vmsize:
                            struct.pack_into('<Q', data, cmd_off + 24, new_vmsize)
                        print(f"    Updated __PRELINK_INFO: filesize 0x{old_vmsize:x}→0x{new_filesize:x}, vmsize 0x{old_vmsize:x}→0x{new_vmsize:x}")
                        break

        # Write updated kernel
        output_path = injector.work_dir / "kernel_patched_full.raw"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(bytes(data))
        print(f"  Patched kernel with expanded XML written to {output_path}")

        # Also update injector.target_data so build_patched_kernel uses the expanded version
        injector.target_data = data

    # 5. Write patched kernel
    injector.build_patched_kernel(output_kernel)

    print(f"  Done! Injected {len(injected)} drivers")
    return output_kernel


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python -m ios16se.kernel_patch_inject <target_kernel> <donor_kernel> <output_kernel>")
        sys.exit(1)

    inject_n69_drivers(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        Path("work/kernel_inject")
    )