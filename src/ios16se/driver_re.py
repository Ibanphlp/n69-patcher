"""Driver reverse engineering toolkit for iOS 16 SE kernel patches.

Extracts, analyzes, and compares prelinked kexts from kernelcache.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import struct

from .prelink import inspect_bytes, extract_driver, metadata, bundle_ids_from_prelink
from .macho import inspect_bytes as inspect_macho_bytes, Segment as MachOSegment


@dataclass
class DriverInfo:
    """Complete information about a prelinked driver."""
    bundle_id: str
    load_addr: int
    size: int
    dependencies: list[str]
    segment: Optional[MachOSegment] = None
    extracted_path: Optional[Path] = None


class DriverRE:
    """Reverse engineering toolkit for prelinked kernel drivers."""

    def __init__(self, kernel_path: Path, work_dir: Path):
        self.kernel_path = kernel_path
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._kernel_data: Optional[bytes] = None
        self._macho = None
        self._prelink_data: Optional[bytes] = None

    def load_kernel(self):
        """Load kernel and parse prelink info."""
        self._kernel_data = self.kernel_path.read_bytes()
        self._macho = inspect_macho_bytes(self._kernel_data)

        # Find __PRELINK_INFO segment
        for seg in self._macho.segments:
            if seg.name == "__PRELINK_INFO":
                self._prelink_data = self._kernel_data[seg.fileoff:seg.fileoff + seg.filesize]
                break

        if self._prelink_data is None:
            raise ValueError("__PRELINK_INFO not found")

    def list_drivers(self) -> list[DriverInfo]:
        """List all prelinked drivers with metadata."""
        if self._prelink_data is None:
            self.load_kernel()

        inventory = bundle_ids_from_prelink(self._prelink_data)
        drivers = []

        for bundle_id in inventory.bundle_identifiers:
            try:
                meta = metadata(self.kernel_path, bundle_id)
                drivers.append(DriverInfo(
                    bundle_id=bundle_id,
                    load_addr=meta.executable_load_address,
                    size=meta.executable_size,
                    dependencies=list(meta.dependencies),
                ))
            except Exception as e:
                print(f"  Warning: Failed to get metadata for {bundle_id}: {e}")

        return drivers

    def extract_driver(self, bundle_id: str) -> Path:
        """Extract a driver Mach-O to work directory."""
        output = self.work_dir / f"{bundle_id}.kext"
        if output.exists():
            import shutil
            shutil.rmtree(output)
        output.mkdir(parents=True)

        macho_path = output / f"{bundle_id}.raw"
        extract_driver(self.kernel_path, bundle_id, macho_path)

        driver = next((d for d in self.list_drivers() if d.bundle_id == bundle_id), None)
        if driver:
            driver.extracted_path = macho_path

        return macho_path

    def extract_all_drivers(self) -> list[DriverInfo]:
        """Extract all drivers to work directory."""
        drivers = self.list_drivers()
        for d in drivers:
            self.extract_driver(d.bundle_id)
        return drivers

    def find_driver_in_prelink(self, bundle_id: str) -> Optional[dict]:
        """Find driver's XML entry in __PRELINK_INFO."""
        if self._prelink_data is None:
            self.load_kernel()

        needle = f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>".encode()
        for match in re.finditer(re.escape(needle), self._prelink_data):
            start = max(0, match.start() - 8192)
            end = match.end() + 16384
            record = self._prelink_data[start:end]

            # Parse key fields
            result = {"bundle_id": bundle_id, "xml_offset": match.start()}

            for key, pattern in [
                ("_PrelinkExecutableLoadAddr", rb"<key>_PrelinkExecutableLoadAddr</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>"),
                ("_PrelinkExecutableSize", rb"<key>_PrelinkExecutableSize</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>"),
                ("OSBundleLibraries", rb"<key>OSBundleLibraries</key><dict>(.*?)</dict>"),
            ]:
                m = re.search(pattern, record, re.DOTALL)
                if m:
                    if key == "OSBundleLibraries":
                        libs = re.findall(rb"<key>([^<]+)</key><string>", m.group(1))
                        result[key] = [l.decode() for l in libs]
                    else:
                        result[key] = int(m.group(1), 16)

            return result

        return None

    def diff_drivers(self, other_kernel: Path, bundle_id: str) -> dict:
        """Compare a driver between two kernels."""
        # Extract both
        my_driver = self.extract_driver(bundle_id)

        other_re = DriverRE(other_kernel, self.work_dir / f"other_{bundle_id}")
        other_driver = other_re.extract_driver(bundle_id)

        # Run diff
        result = subprocess.run(
            ["diff", "-u", str(my_driver), str(other_driver)],
            capture_output=True, text=True
        )

        return {
            "bundle_id": bundle_id,
            "my_driver": str(my_driver),
            "other_driver": str(other_driver),
            "identical": result.returncode == 0,
            "diff": result.stdout if result.returncode != 0 else "",
        }

    def analyze_driver_macho(self, bundle_id: str) -> dict:
        """Analyze extracted driver Mach-O structure."""
        if not hasattr(self, '_extracted_cache'):
            self._extracted_cache = {}

        if bundle_id not in self._extracted_cache:
            macho_path = self.extract_driver(bundle_id)
            self._extracted_cache[bundle_id] = inspect_macho_bytes(macho_path.read_bytes())

        macho = self._extracted_cache[bundle_id]

        return {
            "bundle_id": bundle_id,
            "cputype": f"0x{macho.header.cputype:x}",
            "cpusubtype": f"0x{macho.header.cpusubtype:x}",
            "filetype": macho.header.filetype,
            "ncmds": macho.header.ncmds,
            "segments": [
                {
                    "name": seg.name,
                    "vmaddr": f"0x{seg.vmaddr:x}",
                    "vmsize": f"0x{seg.vmsize:x}",
                    "fileoff": f"0x{seg.fileoff:x}",
                    "filesize": f"0x{seg.filesize:x}",
                    "nsects": seg.nsects,
                }
                for seg in macho.segments
            ],
            "sections": [
                {
                    "name": sect.name,
                    "segname": sect.segname,
                    "addr": f"0x{sect.addr:x}",
                    "size": f"0x{sect.size:x}",
                    "offset": f"0x{sect.offset:x}",
                }
                for seg in macho.segments
                for sect in getattr(seg, 'sections', [])
            ],
        }


def find_driver_by_name(drivers: list[DriverInfo], name_pattern: str) -> list[DriverInfo]:
    """Find drivers matching a name pattern."""
    import fnmatch
    return [d for d in drivers if fnmatch.fnmatch(d.bundle_id.lower(), name_pattern.lower())]


# Target driver list for N69AP iOS 16 port
TARGET_DRIVERS = {
    # PMU / Power
    "pmu": ["AppleD2255PMU", "AppleChestnut*"],
    # Touch
    "touch": ["AppleMultitouchSPIN69", "AppleMultitouch*"],
    # Display
    "display": ["ApplePinot*", "AppleDSIM*", "AppleMipi*"],
    # Audio
    "audio": ["AppleCS42L71*", "AppleCS35L19*", "AppleAudio*"],
    # Charger
    "charger": ["AppleC26*", "AppleCharger*"],
    # WiFi
    "wifi": ["AppleBCM*", "AppleWiFi*", "IO80211*"],
    # Haptic
    "haptic": ["AppleDRV2604*", "AppleHaptic*"],
    # SEP
    "sep": ["AppleSEP*", "AppleSecureEnclave*"],
}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m ios16se.driver_re <kernel.raw> [--extract-all] [--list]")
        sys.exit(1)

    kernel = Path(sys.argv[1])
    work = Path("work/driver_re")

    re_tool = DriverRE(kernel, work)
    re_tool.load_kernel()

    if "--list" in sys.argv:
        drivers = re_tool.list_drivers()
        for d in drivers:
            print(f"  {d.bundle_id}: addr=0x{d.load_addr:x} size=0x{d.size:x} deps={len(d.dependencies)}")

    if "--extract-all" in sys.argv:
        drivers = re_tool.extract_all_drivers()
        print(f"Extracted {len(drivers)} drivers to {work}")

    if "--analyze" in sys.argv:
        idx = sys.argv.index("--analyze")
        if idx + 1 < len(sys.argv):
            bundle_id = sys.argv[idx + 1]
            info = re_tool.analyze_driver_macho(bundle_id)
            import json
            print(json.dumps(info, indent=2))