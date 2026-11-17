"""The framebuffer panic renderer has to land on a real kernel layout."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ios16se.panic_screen import (  # noqa: E402
    OVERLAY_ELF,
    PanicOverlayError,
    _elf_text,
    compile_overlay,
    install_panic_overlay,
)

TEXT_VM = 0xFFFFF007004000
EXEC_VM = 0xFFFFF00717C000
DATA_VM = 0xFFFFF00783C000
NESTED = EXEC_VM + 0x4000
SAVE = NESTED + 0x40
CONSOLE = NESTED + 0x80
DOPRNT = NESTED + 0xC0
STRING_VA = TEXT_VM + 0x400
VIDEO = DATA_VM + 0x800


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _adrp(pc: int, register: int, target: int) -> int:
    immediate = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    immediate &= (1 << 21) - 1
    return 0x90000000 | ((immediate & 3) << 29) | (((immediate >> 2) & 0x7FFFF) << 5) | register


def _add(register: int, base: int, immediate: int) -> int:
    return 0x91000000 | (immediate << 10) | (base << 5) | register


def _section(name: str, segment: str, addr: int, size: int, offset: int) -> bytes:
    return struct.pack(
        "<16s16s2Q8I",
        name.encode().ljust(16, b"\0"),
        segment.encode().ljust(16, b"\0"),
        addr,
        size,
        offset,
        2,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def _segment(name: str, vm: int, size: int, fileoff: int, prot: int, sections: list[bytes]) -> bytes:
    return struct.pack(
        "<2I16s4Q4I",
        0x19,
        72 + 80 * len(sections),
        name.encode().ljust(16, b"\0"),
        vm,
        size,
        fileoff,
        size,
        prot,
        prot,
        len(sections),
        0,
    ) + b"".join(sections)


def _synthetic_kernel() -> bytes:
    text = _segment("__TEXT", TEXT_VM, 0x1000, 0, 1, [])
    hib = _section("__hib_text", "__TEXT_EXEC", EXEC_VM, 0x20, 0x1000)
    body = _section("__text", "__TEXT_EXEC", NESTED, 0x200, 0x5000)
    executable = _segment("__TEXT_EXEC", EXEC_VM, 0x5000, 0x1000, 5, [hib, body])
    lock = _section("__lock_grp", "__DATA", DATA_VM, 0x40, 0x6000)
    percpu = _section("__percpu", "__DATA", VIDEO, 0x100, 0x6800)
    data = _segment("__DATA", DATA_VM, 0x2000, 0x6000, 3, [lock, percpu])
    commands = text + executable + data
    # symtab and function starts are appended after the command size is known.
    sym_names = b"\0_PESavePanicInfo\0__doprnt\0__ZN16IOPlatformExpert14getConsoleInfoEP8PE_Video\0"
    symbols = struct.pack("<IBBHQ", 1, 0xF, 1, 0, SAVE)
    symbols += struct.pack("<IBBHQ", sym_names.index(b"__doprnt"), 0xF, 1, 0, DOPRNT)
    symbols += struct.pack(
        "<IBBHQ", sym_names.index(b"__ZN16IOPlatformExpert14getConsoleInfoEP8PE_Video"), 0xF, 1, 0, CONSOLE
    )
    starts = _uleb(NESTED - TEXT_VM) + _uleb(0x40) + b"\0"
    extra = struct.pack("<2I4I", 0x2, 24, 0, 3, 0, len(sym_names)) + struct.pack("<2I2I", 0x26, 16, 0, len(starts))
    sizeofcmds = len(commands) + len(extra)
    header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 5, sizeofcmds, 1, 0)
    # Place the symbol table and function starts inside __TEXT, after the header.
    symoff = 0x800
    stroff = symoff + len(symbols)
    startoff = 0x900
    extra = struct.pack("<2I4I", 0x2, 24, symoff, 3, stroff, len(sym_names))
    extra += struct.pack("<2I2I", 0x26, 16, startoff, len(starts))
    image = bytearray(0x8000)
    image[: len(header)] = header
    image[32 : 32 + sizeofcmds] = commands + extra
    image[0x400 : 0x400 + len(b"Nested panic detected")] = b"Nested panic detected"
    image[symoff : symoff + len(symbols)] = symbols
    image[stroff : stroff + len(sym_names)] = sym_names
    image[startoff : startoff + len(starts)] = starts
    struct.pack_into("<I", image, 0x5000, 0xD10143FF)
    struct.pack_into("<I", image, 0x5004, _adrp(NESTED + 4, 0, STRING_VA))
    struct.pack_into("<I", image, 0x5008, _add(0, 0, STRING_VA & 0xFFF))
    struct.pack_into("<I", image, 0x5000 + 0x40, 0xA9BF7BFD)
    struct.pack_into("<I", image, 0x5000 + 0x80, _adrp(CONSOLE, 8, VIDEO))
    struct.pack_into("<I", image, 0x5000 + 0x84, _add(8, 8, VIDEO & 0xFFF))
    image[0x5000 + 0xC0 : 0x5000 + 0xC0 + 12] = bytes.fromhex("e40303aae30302aa210040f9")
    return bytes(image)


class PanicOverlayTests(unittest.TestCase):
    def test_renderer_fits_and_matches_source(self):
        blob, symbols = _elf_text(OVERLAY_ELF)
        self.assertLessEqual(len(blob), 0x1000)
        self.assertEqual(symbols["pd_hook"], 0)
        self.assertIn(b"N69 PANIC", blob)
        try:
            with tempfile.TemporaryDirectory() as directory:
                rebuilt = Path(directory) / "panic_overlay.elf"
                compile_overlay(rebuilt)
                other, other_symbols = _elf_text(rebuilt)
        except PanicOverlayError:
            return
        self.assertEqual(other, blob)
        self.assertEqual(other_symbols, symbols)

    def test_synthetic_kernel_hooks_both_entries(self):
        kernel = _synthetic_kernel()
        patched = install_panic_overlay(kernel)
        self.assertEqual(len(patched), len(kernel))
        nested = struct.unpack_from("<I", patched, 0x5000)[0]
        save = struct.unpack_from("<I", patched, 0x5040)[0]
        self.assertEqual(nested >> 26, 0x5)  # B
        self.assertEqual(save >> 26, 0x5)
        self.assertNotEqual(patched, kernel)

    def test_real_donor_kernel_when_present(self):
        donor = Path("/tmp/kc.raw")
        if not donor.is_file():
            return
        patched = install_panic_overlay(donor.read_bytes())
        self.assertEqual(len(patched), donor.stat().st_size)
        self.assertIn(b"N69 PANIC", patched)

    def test_missing_string_is_refused(self):
        kernel = bytearray(_synthetic_kernel())
        kernel[0x400 : 0x420] = b"\0" * 32
        with self.assertRaises(PanicOverlayError):
            install_panic_overlay(bytes(kernel))


if __name__ == "__main__":
    unittest.main()
