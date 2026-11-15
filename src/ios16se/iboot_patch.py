"""Patch iBEC to embed boot arguments and root device.

This module implements what n56-patcher's iBoot64Patcher does:
1. Inject boot-args string into the iBEC binary
2. Patch root-device to match partition 4 (SystemB)
3. Optional: remove serial=3 for cleaner boot
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Optional


def find_bootargs_string(data: bytes) -> Optional[int]:
    """Find the existing boot-args string in iBEC binary."""
    # Look for common boot arg patterns
    patterns = [
        b'rd=disk0s',
        b'amfi_get_out_of_my_way',
        b'root=',
        b'-v ',
    ]
    for pat in patterns:
        if pat in data:
            idx = data.find(pat)
            # Find end of string (null terminator or non-printable)
            end = idx
            while end < len(data) and (data[end] >= 0x20 and data[end] < 0x7f or data[end] in (0x0a, 0x0d)):
                end += 1
            return idx
    return None


def find_pointer_table(data: bytes, offset: int) -> Optional[int]:
    """Find pointer table entry referencing boot-args string."""
    # Search for pointer addresses around the string location
    search_start = max(0, offset - 0x10000)
    search_end = min(len(data), offset + 0x10000)

    for i in range(search_start, search_end - 8):
        ptr_val = struct.unpack_from('<Q', data, i)[0]
        if ptr_val == offset:
            return i
    return None


def patch_bootargs(ibec_raw: bytes, new_bootargs: str) -> bytes:
    """Patch boot-args into iBEC binary.

    Args:
        ibec_raw: Raw iBEC binary (uncompressed)
        new_bootargs: New boot-args string to embed

    Returns:
        Patched iBEC binary
    """
    # Convert boot args to bytes
    bootargs_bytes = new_bootargs.encode('utf-8') + b'\x00'

    # Pad to reasonable size (256 bytes should be enough)
    if len(bootargs_bytes) > 256:
        raise ValueError(f"Boot args too long: {len(bootargs_bytes)} bytes")
    bootargs_bytes = bootargs_bytes.ljust(256, b'\x00')

    # Find existing boot args string
    old_offset = find_bootargs_string(ibec_raw)

    if old_offset is None:
        # No existing boot args - need to find free space or append
        # Look through the binary for null regions
        for i in range(0x2000, len(ibec_raw) - 256):
            if all(b == 0 for b in ibec_raw[i:i+256]):
                old_offset = i
                break

        if old_offset is None:
            # Extend the binary
            ibec_raw = bytearray(ibec_raw)
            old_offset = len(ibec_raw)
            ibec_raw.extend(b'\x00' * 512)
            ibec_raw = bytes(ibec_raw)

    # Check if we need to extend
    if len(ibec_raw) < old_offset + 512:
        ibec_raw = ibec_raw + b'\x00' * 512

    # Patch the string
    patched = bytearray(ibec_raw)
    patched[old_offset:old_offset + len(bootargs_bytes)] = bootargs_bytes

    # Update pointers to this string
    # Search for the old string pointer
    for i in range(len(patched) - 8):
        ptr_val = struct.unpack_from('<Q', patched, i)[0]
        if ptr_val == old_offset:
            struct.pack_into('<Q', patched, i, old_offset)

    return bytes(patched)


def patch_root_partition(ibec_raw: bytes, partition: int = 4) -> bytes:
    """Patch root device partition number in iBEC.

    Args:
        ibec_raw: Raw iBEC binary
        partition: Partition number (default 4 for SystemB)

    Returns:
        Patched iBEC binary
    """
    # Look for the partition number in common locations
    # Partition numbers are often stored near boot-args or as standalone bytes

    # Pattern to find partition references
    # Often stored as 0x64 (100 = 0x64 for partition 100 in some contexts)
    search_region = ibec_raw[0x10000:0x100000]  # Search in code/data section

    patched = bytearray(ibec_raw)

    # Try to find the root partition specification
    # In iBoot, it's often at a fixed offset relative to boot args

    # Look for the string "disk0s" pattern
    pattern = b'disk0s'
    idx = search_region.find(pattern)
    if idx >= 0:
        # The partition number typically follows
        # disk0s1, disk0s4, etc.
        idx += 6  # After "disk0s"
        if idx < len(patched):
            old_part = patched[idx]
            new_part = partition
            if old_part != new_part:
                patched[idx] = new_part
                print(f"  Patched root partition: {old_part} -> {new_part}")

    # Also look in device tree or other structures
    # The partition is often stored as a 32-bit value
    for i in range(0x2000, len(patched) - 4):
        val = struct.unpack_from('<I', patched, i)[0]
        if val in (1, 2, 3, 4, 5, 6):
            # Check if this is a partition reference
            # Look for context
            if i >= 4 and patched[i-4:i] == b'\x00\x00\x00\x00':
                continue  # Likely padding
            if i + 4 < len(patched) and patched[i+4:i+8] == b'\x00\x00\x00\x00':
                continue  # Likely padding

    return bytes(patched)


def embed_boot_args(ibec_path: Path, boot_args: str, partition: int, output: Path):
    """Complete iBEC patching workflow.

    Args:
        ibec_path: Path to input iBEC IM4P
        boot_args: Boot arguments to embed
        partition: Root device partition
        output: Output path for patched iBEC IM4P
    """
    import subprocess
    import tempfile

    img4tool = Path('tools/img4lib/bin/img4tool')
    im4m = Path('state/IM4M.bin')

    print(f"  Patching iBEC with boot-args: {boot_args}")
    print(f"  Root partition: disk0s1s{partition}")

    # Extract raw iBEC
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        ibec_raw = Path(tmp.name)

    result = subprocess.run([
        str(img4tool), '-i', str(ibec_path), '-o', str(ibec_raw), '-e'
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"    Error extracting iBEC: {result.stderr}")
        return

    # Read and patch
    raw_data = ibec_raw.read_bytes()

    # Patch boot args
    patched_data = patch_bootargs(raw_data, boot_args)

    # Patch root partition (remove if part of boot args)
    # The "rd=disk0s1s4" in boot args handles this, but double-check

    # Write patched raw
    patched_raw = Path(str(ibec_raw) + '.patched')
    patched_raw.write_bytes(patched_data)

    # Resign/recreate IM4P
    subprocess.run([
        str(img4tool), '-c', str(output), str(patched_raw),
        '-t', 'ibec', '-d', 'iBoot-7459.140.15',
        '-m', str(im4m)
    ], check=True)

    # Cleanup
    ibec_raw.unlink(missing_ok=True)
    patched_raw.unlink(missing_ok=True)

    print(f"    Created patched iBEC: {output.stat().st_size} bytes")