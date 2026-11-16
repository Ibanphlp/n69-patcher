"""Rewrite the iOS 16 SEP request builder down to the v2 layout.

The anchor is the single ``mov w8, #3`` in the iPad 16.7.16 kernel that is
followed by the same store sequence SevenUP's AK1 checkpoint edits. Every
store keeps its original register and is only moved to the v2 offset, and
each site asserts its before-bytes. A missing or ambiguous site fails the
build. Function prologues are not replaced with ``ret``.

The two n56 legacy edits (class-M upgrade redirect, locker capability
returning 0) are applied only when their before-bytes occur exactly once in
AppleSEPKeyStore. On 20H392 those iOS 13 bytes are absent, so they are
recorded as not applicable. A partial or repeated match fails the build.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "SevenUP-main" / "src"))

from iphone7_port.kc import KC  # noqa: E402
from iphone7_port.kernel_chestnut import adrp_page, encode_adrp, signed  # noqa: E402

PROLOGUE_STP = bytes.fromhex("fd7bbfa9")
RET = bytes.fromhex("c0035fd6")
NOP = 0xD503201F
ANCHOR = bytes.fromhex("68008052e8c300b9e90b40f9e81340f9e9a30ca9")
# Offsets from the mov w8, #3, copied from SevenUP kernel_ak1.py.
# The 20H392 builder has the same words at the same offsets.
AK1_EDITS = [
    (0x000, 0x52800068, 0x52800048, "Command 15 request version 3 -> 2"),
    (0x00C, 0xF94013E8, NOP, "Omit v3-only auxiliary input pointer"),
    (0x010, 0xA90CA3E9, 0xF90067E9, "Store operation alone at the v2 slot"),
    (0x014, 0xB94033E8, NOP, "Omit v3-only auxiliary input length"),
    (0x018, 0xB900DBE8, NOP, "Remove v3-only input length slot"),
]
AK1_STORES = [
    (0x020, 0xF90073E8, 8), (0x02C, 0x291D23E9, 4), (0x030, 0x291E5BF4, 4),
    (0x034, 0xB900FBFA, 4), (0x038, 0xF90083F7, 8), (0x03C, 0xB9010BF9, 4),
    (0x040, 0xF9008BF8, 8), (0x048, 0xB9011BE8, 4), (0x050, 0xF90093E8, 8),
    (0x058, 0xB9012BE8, 4), (0x060, 0xB9012FE8, 4), (0x068, 0xF9009BE8, 8),
    (0x070, 0xB9013BE8, 4),
]
AK1_TAIL = [
    (0x078, 0xF900A3E8, NOP, "Omit v3-only trailing auxiliary pointer"),
    (0x080, 0xB9014BE8, NOP, "Omit v3-only trailing auxiliary length"),
    (0x084, 0xA9157FFF, 0xA9137FFF, "Initialize legacy output slots 32 bytes earlier"),
]
AK1_OUTPUTS = [
    (0x090, 0xF900ABE8, 8), (0x09C, 0xB9015BE8, 4), (0x0A8, 0xB9015FE8, 4),
    (0x0CC, 0xF940ABE8, 8), (0x0D8, 0xB9415BF4, 4), (0x0E0, 0xF940ABE0, 8),
    (0x0F4, 0xF940ABE0, 8), (0x104, 0xB9415BE8, 4), (0x110, 0xB9415FE8, 4),
]
CLASS_M_BEFORE = bytes.fromhex("686a40f9088d42f9")
CLASS_M_AFTER = struct.pack("<II", 0x52800008, 0x14000069)
LOCKER_BEFORE = bytes.fromhex("ff8300d1fd7b01a9")
LOCKER_AFTER = struct.pack("<II", 0x52800000, 0xD65F03C0)
FUNCTION_BACK = 0x104
FUNCTION_FORWARD = 0x16C


class SepShimError(RuntimeError):
    """A reviewed SEP site is missing, ambiguous, or not the expected bytes."""


def _va_for_offset(kernel: KC, fileoff: int) -> int:
    for segment in kernel.segments:
        base = kernel.base + segment["offset"]
        if base <= fileoff < base + segment["size"]:
            return segment["addr"] + (fileoff - base)
    raise SepShimError("SEP anchor is not in a mapped segment")


def _branch(pc: int, target: int, link: bool = False) -> int:
    delta = target - pc
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise SepShimError(f"branch from {pc:#x} to {target:#x} is out of range")
    opcode = 0x94000000 if link else 0x14000000
    return opcode | ((delta // 4) & 0x3FFFFFF)


def _shift_store(word: int, scale: int, delta: int) -> int:
    bit = 15 if (word & 0x3B000000) == 0x29000000 else 10
    return word - ((delta // scale) << bit)


def _find_hole(kernel: KC, size: int) -> int:
    """Return a VA inside some kext __TEXT_EXEC that is ``size`` zero bytes."""

    for _name, entry in kernel.kexts.items():
        if not entry.get("_PrelinkExecutableLoadAddr"):
            continue
        for segment in kernel.ksegments(_name):
            if segment["name"] != "__TEXT_EXEC" or segment["size"] < size:
                continue
            blob = kernel.read(segment["addr"], segment["size"])
            index = blob.find(b"\0" * size)
            while index >= 0:
                if index % 4 == 0:
                    return segment["addr"] + index
                index = blob.find(b"\0" * size, index + 1)
    raise SepShimError("no unused __TEXT_EXEC span for the legacy SEP constructor")


def _hooks(kernel: KC, target: int) -> list[int]:
    found = []
    for name in ("com.apple.driver.AppleSEPManager", "com.apple.driver.AppleSEPKeyStore"):
        for segment in kernel.ksegments(name):
            if segment["name"] != "__TEXT_EXEC":
                continue
            blob = kernel.read(segment["addr"], segment["size"])
            for index in range(0, len(blob) - 3, 4):
                word = struct.unpack_from("<I", blob, index)[0]
                if word & 0xFC000000 != 0x94000000:
                    continue
                pc = segment["addr"] + index
                dest = pc + signed(word & 0x3FFFFFF, 26) * 4
                if dest == target:
                    found.append(pc)
    if not found:
        raise SepShimError("no BL targets the SEP request constructor")
    return found


def _legacy_sites(kernel: KC) -> list[dict]:
    """Apply the two n56 edits only when the before-bytes are unique."""

    sites = []
    for bundle, before, after, reason in (
        ("com.apple.driver.AppleSEPKeyStore", CLASS_M_BEFORE, CLASS_M_AFTER,
         "class-M upgrade redirect"),
        ("com.apple.driver.AppleSEPKeyStore", LOCKER_BEFORE, LOCKER_AFTER,
         "locker capability variable 7 returns 0"),
    ):
        hits = []
        for segment in kernel.ksegments(bundle):
            if not segment["size"]:
                continue
            blob = kernel.read(segment["addr"], segment["size"])
            start = 0
            while True:
                index = blob.find(before, start)
                if index < 0:
                    break
                hits.append(segment["addr"] + index)
                start = index + 1
        if len(hits) != 1:
            sites.append({
                "reason": reason, "applied": False,
                "detail": f"before-bytes matched {len(hits)} times; not a unique site on this kernel",
            })
            continue
        sites.append({"reason": reason, "applied": True, "va": hits[0],
                      "before": before, "after": after})
    return sites


def apply_sep(kernel_path: Path, output: Path) -> dict:
    kernel = KC(kernel_path)
    hits = []
    start = 0
    while True:
        index = kernel.data.find(ANCHOR, start)
        if index < 0:
            break
        hits.append(index)
        start = index + 1
    if len(hits) != 1:
        raise SepShimError(f"SEP request anchor matched {len(hits)} times")
    anchor = _va_for_offset(kernel, hits[0])
    original = anchor - FUNCTION_BACK
    end = anchor + FUNCTION_FORWARD
    if struct.unpack("<I", kernel.read(original, 4))[0] != 0xD10743FF:
        raise SepShimError("SEP constructor prologue is not the reviewed sub sp")
    edits = []

    def word_at(va: int) -> int:
        return struct.unpack("<I", kernel.read(va, 4))[0]

    def plan(va: int, before: int, after: int, reason: str) -> None:
        actual = word_at(va)
        if actual != before:
            raise SepShimError(f"{reason} at {va:#x}: {actual:#x} != {before:#x}")
        if struct.pack("<I", before) in (PROLOGUE_STP, bytes.fromhex("fd7b01a9")) and after == 0xD65F03C0:
            raise SepShimError("refusing to replace a function prologue with ret")
        edits.append({"va": va, "before": before, "after": after, "reason": reason})

    for offset, before, after, reason in AK1_EDITS:
        plan(anchor + offset, before, after, reason)
    for offset, before, scale in AK1_STORES:
        plan(anchor + offset, before, _shift_store(before, scale, 16),
             "Place legacy request field at v2 offset")
    for offset, before, after, reason in AK1_TAIL:
        plan(anchor + offset, before, after, reason)
    for offset, before, scale in AK1_OUTPUTS:
        plan(anchor + offset, before, _shift_store(before, scale, 32),
             "Read/write legacy output slot")

    # The 20H392 kernel has no unused __TEXT_EXEC span large enough for AK1's
    # relocated copy (the constructor is 0x270 bytes; the largest hole is 246).
    # The same before-bytes are therefore rewritten in place. AK2's guard edits
    # only exist once that copy has been planted, so they are not invented.
    out = bytearray(kernel.data)
    for edit in edits:
        struct.pack_into("<I", out, kernel.offset(edit["va"]), edit["after"])
    legacy_notes = _legacy_sites(kernel)
    for site in legacy_notes:
        if not site["applied"]:
            continue
        offset = kernel.offset(site["va"])
        if out[offset:offset + len(site["before"])] != site["before"]:
            raise SepShimError(site["reason"] + " before-bytes changed")
        out[offset:offset + len(site["after"])] = site["after"]

    for edit in edits:
        before = struct.pack("<I", edit["before"])
        after = struct.pack("<I", edit["after"])
        if before == PROLOGUE_STP and after == RET:
            raise SepShimError("function prologue was replaced with ret")
    if len(out) != len(kernel.data):
        raise SepShimError("SEP shim changed the kernel size")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(out)
    return {
        "anchor": hex(anchor),
        "constructor": hex(original),
        "legacy_copy": None,
        "edits": [{**item, "va": hex(item["va"]), "before": f"{item['before']:08x}",
                   "after": f"{item['after']:08x}"} for item in edits],
        "relocated": [],
        "legacy_sep": legacy_notes,
        "prologue_replaced_with_ret": False,
    }


def prologue_was_replaced_with_ret(before: bytes, after: bytes) -> bool:
    """True when a ``stp x29, x30`` prologue was overwritten by a lone ``ret``."""

    if len(before) != len(after):
        return False
    for index in range(0, len(before) - 3):
        if before[index:index + 4] == PROLOGUE_STP and after[index:index + 4] == RET:
            return True
    return False
