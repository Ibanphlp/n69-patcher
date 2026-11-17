"""Draw a kernel panic on the SE framebuffer.

The hook is the same idea as SevenUP's panic overlay: the first instruction of
the nested-panic function and of ``PESavePanicInfo`` jumps into a small
renderer, which writes the panic text into the ``PE_Video`` framebuffer.
Nothing is drawn until that framebuffer has a kernel virtual address, which
happens once the display is up.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .kc_patch import KernelPatch, PatchContext
from .macho import MachOError, inspect_bytes

OVERLAY_ELF = Path(__file__).with_name("panic_overlay.elf")
OVERLAY_SOURCES = (
    Path(__file__).with_name("panic_overlay.s"),
    Path(__file__).with_name("panic_overlay.c"),
    Path(__file__).with_name("panic_overlay.ld"),
)


def compile_overlay(destination: Path) -> None:
    """Rebuild the renderer ELF. The boot path uses the committed file."""

    import shutil
    import subprocess

    clang = shutil.which("clang")
    lld = shutil.which("ld.lld-18") or shutil.which("ld.lld")
    if clang is None or lld is None:
        raise PanicOverlayError("clang and lld are required to rebuild the panic renderer")
    assembly, source, script = OVERLAY_SOURCES
    subprocess.run(
        [
            clang, "-target", "aarch64-linux-gnu", "-ffreestanding", "-fno-pic", "-Os", "-static",
            "-fno-stack-protector", "-fno-builtin", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
            "-nostdlib", f"-fuse-ld={lld}", "-Wl,-no-pie", "-Wl,--no-dynamic-linker", "-Wl,-nmagic",
            f"-Wl,-T,{script}", "-Wl,--build-id=none", str(assembly), str(source), "-o", str(destination),
        ],
        check=True,
    )
NESTED_PROLOGUE = 0xD10143FF  # sub sp, sp, #0x50
SAVE_PROLOGUE = 0xA9BF7BFD  # stp x29, x30, [sp, #-0x10]!
DOPRNT_PROLOGUE = bytes.fromhex("e40303aae30302aa210040f9")
STATE_BYTES = 1024
PAGE = 0x1000


class PanicOverlayError(RuntimeError):
    """The panic renderer cannot be placed without guessing."""


@dataclass(frozen=True)
class _Section:
    name: str
    addr: int
    size: int


@dataclass(frozen=True)
class _Segment:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    initprot: int
    sections: tuple[_Section, ...]


@dataclass(frozen=True)
class _Image:
    data: bytearray
    segments: tuple[_Segment, ...]
    symbols: dict[str, int]
    functions: tuple[int, ...]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _segments(data: bytes) -> tuple[_Segment, ...]:
    info = inspect_bytes(data)
    # inspect_bytes does not return section names, so walk the same commands.
    if data[:4] != b"\xcf\xfa\xed\xfe":
        raise PanicOverlayError("panic overlay expects a little-endian 64-bit kernel")
    _magic, _cpu, _sub, _filetype, ncmds, sizeofcmds, _flags, _reserved = struct.unpack_from("<8I", data)
    cursor = 32
    end = 32 + sizeofcmds
    found: list[_Segment] = []
    for _ in range(ncmds):
        command, size = struct.unpack_from("<2I", data, cursor)
        if command == 0x19 and size >= 72:
            name = data[cursor + 8 : cursor + 24].split(b"\0", 1)[0].decode("ascii", "replace")
            vmaddr, vmsize, fileoff, filesize, _maxprot, initprot, nsects, _segflags = struct.unpack_from(
                "<4Q4I", data, cursor + 24
            )
            sections: list[_Section] = []
            section_cursor = cursor + 72
            for _section in range(nsects):
                if section_cursor + 80 > cursor + size:
                    raise PanicOverlayError(f"{name} section list is truncated")
                section_name = data[section_cursor : section_cursor + 16].split(b"\0", 1)[0].decode("ascii", "replace")
                addr, sec_size = struct.unpack_from("<2Q", data, section_cursor + 32)
                sections.append(_Section(section_name, addr, sec_size))
                section_cursor += 80
            found.append(_Segment(name, vmaddr, vmsize, fileoff, filesize, initprot, tuple(sections)))
        cursor += size
        if cursor > end:
            raise PanicOverlayError("Mach-O command stream overran its declared size")
    if not found:
        raise PanicOverlayError("kernel has no segments")
    _ = info
    return tuple(found)


def _symbols(data: bytes) -> dict[str, int]:
    _magic, _cpu, _sub, _filetype, ncmds, sizeofcmds, _flags, _reserved = struct.unpack_from("<8I", data)
    cursor = 32
    for _ in range(ncmds):
        command, size = struct.unpack_from("<2I", data, cursor)
        if command == 0x2 and size >= 24:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<4I", data, cursor + 8)
            strings = data[stroff : stroff + strsize]
            found: dict[str, int] = {}
            for index in range(nsyms):
                name_off, _ntype, _nsect, _ndesc, value = struct.unpack_from("<IBBHQ", data, symoff + index * 16)
                if name_off >= len(strings):
                    continue
                name = strings[name_off:].split(b"\0", 1)[0].decode("ascii", "replace")
                if name:
                    found[name] = value
            return found
        cursor += size
    raise PanicOverlayError("kernel has no symbol table")


def _functions(data: bytes, text_vm: int) -> tuple[int, ...]:
    _magic, _cpu, _sub, _filetype, ncmds, sizeofcmds, _flags, _reserved = struct.unpack_from("<8I", data)
    cursor = 32
    for _ in range(ncmds):
        command, size = struct.unpack_from("<2I", data, cursor)
        if command == 0x26 and size >= 16:
            dataoff, datasize = struct.unpack_from("<2I", data, cursor + 8)
            blob = data[dataoff : dataoff + datasize]
            address = text_vm
            starts = []
            index = 0
            while index < len(blob) and blob[index] != 0:
                value = 0
                shift = 0
                while True:
                    byte = blob[index]
                    index += 1
                    value |= (byte & 0x7F) << shift
                    if byte < 0x80:
                        break
                    shift += 7
                address += value
                starts.append(address)
            if not starts:
                raise PanicOverlayError("kernel function-start list is empty")
            return tuple(starts)
        cursor += size
    raise PanicOverlayError("kernel has no function-start list")


def _load(kernel: bytes) -> _Image:
    try:
        segments = _segments(kernel)
    except MachOError as error:
        raise PanicOverlayError(str(error)) from error
    text = next((segment for segment in segments if segment.name == "__TEXT"), None)
    if text is None:
        raise PanicOverlayError("kernel has no __TEXT segment")
    return _Image(bytearray(kernel), segments, _symbols(kernel), _functions(kernel, text.vmaddr))


def _va_offset(image: _Image, va: int) -> int:
    for segment in image.segments:
        if segment.vmaddr <= va < segment.vmaddr + segment.filesize:
            return segment.fileoff + (va - segment.vmaddr)
    raise PanicOverlayError(f"{va:#x} is not in a file-backed segment")


def _word(image: _Image, va: int) -> int:
    return _u32(image.data, _va_offset(image, va))


def _decode_adrp(pc: int, word: int) -> int | None:
    if (word & 0x9F000000) != 0x90000000:
        return None
    immediate = ((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 0x3)
    if immediate & (1 << 20):
        immediate -= 1 << 21
    return (pc & ~0xFFF) + (immediate << 12)


def _decode_add(word: int) -> tuple[int, int, int] | None:
    if (word & 0xFFC00000) != 0x91000000:
        return None
    immediate = (word >> 10) & 0xFFF
    if (word >> 22) & 1:
        immediate <<= 12
    return word & 31, (word >> 5) & 31, immediate


def _encode_branch(source: int, target: int) -> int:
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise PanicOverlayError(f"branch from {source:#x} to {target:#x} does not fit")
    return 0x14000000 | ((delta // 4) & 0x3FFFFFF)


def _executable_ranges(image: _Image) -> list[tuple[int, int]]:
    ranges = []
    for segment in image.segments:
        if segment.initprot & 0x4 and segment.filesize:
            ranges.append((segment.vmaddr, segment.filesize))
    return ranges


def _string_xref(image: _Image, string_va: int) -> int:
    page = string_va & ~0xFFF
    hits = []
    for start, size in _executable_ranges(image):
        for pc in range(start, start + size - 12, 4):
            word = _word(image, pc)
            target_page = _decode_adrp(pc, word)
            if target_page != page:
                continue
            register = word & 31
            for step in (1, 2):
                added = _decode_add(_word(image, pc + step * 4))
                if added and added[1] == register and target_page + added[2] == string_va:
                    hits.append(pc)
                    break
    if len(hits) != 1:
        raise PanicOverlayError(f"nested-panic string has {len(hits)} code references, expected 1")
    return hits[0]


def _function_containing(functions: tuple[int, ...], va: int) -> int:
    start = None
    for function in functions:
        if function <= va:
            start = function
        else:
            break
    if start is None:
        raise PanicOverlayError(f"{va:#x} is before the first function")
    return start


def _video_address(image: _Image) -> int:
    symbol = image.symbols.get("__ZN16IOPlatformExpert14getConsoleInfoEP8PE_Video")
    if symbol is None:
        raise PanicOverlayError("getConsoleInfo is not in the kernel symbol table")
    page = _decode_adrp(symbol, _word(image, symbol))
    added = _decode_add(_word(image, symbol + 4))
    if page is None or added is None or added[1] != (_word(image, symbol) & 31):
        raise PanicOverlayError("getConsoleInfo does not begin with adrp/add of PE_Video")
    return page + added[2]


def _require_symbol(image: _Image, name: str, prologue: bytes | int) -> int:
    try:
        address = image.symbols[name]
    except KeyError as error:
        raise PanicOverlayError(f"{name} is not in the kernel symbol table") from error
    if isinstance(prologue, int):
        found = _word(image, address)
        if found != prologue:
            raise PanicOverlayError(f"{name} prologue is {found:#x}, expected {prologue:#x}")
    else:
        offset = _va_offset(image, address)
        found = bytes(image.data[offset : offset + len(prologue)])
        if found != prologue:
            raise PanicOverlayError(f"{name} prologue changed")
    return address


def _gaps(segment: _Segment) -> list[tuple[int, int]]:
    file_end = segment.vmaddr + segment.filesize
    cursor = segment.vmaddr
    gaps = []
    for section in sorted(segment.sections, key=lambda item: item.addr):
        if section.addr > cursor:
            gaps.append((cursor, section.addr - cursor))
        cursor = max(cursor, section.addr + section.size)
    if file_end > cursor:
        gaps.append((cursor, file_end - cursor))
    return [(start, length) for start, length in gaps if length > 0 and start + length <= file_end]


def _zero_at(image: _Image, va: int, size: int, align: int) -> int:
    for segment in image.segments:
        for start, length in _gaps(segment):
            if not (start <= va < start + length and va + size <= start + length):
                continue
            if va % align:
                return -1
            offset = _va_offset(image, va)
            if bytes(image.data[offset : offset + size]) != bytes(size):
                return -1
            return offset
    return -1


def _choose_cave(image: _Image, blob_size: int, near: int) -> tuple[int, int]:
    choices = []
    for segment in image.segments:
        if not segment.initprot & 0x4:
            continue
        for start, length in _gaps(segment):
            aligned = (start + PAGE - 1) & ~(PAGE - 1)
            if aligned + blob_size > start + length:
                continue
            offset = _va_offset(image, aligned)
            if bytes(image.data[offset : offset + blob_size]) != bytes(blob_size):
                continue
            try:
                _encode_branch(near, aligned)
            except PanicOverlayError:
                continue
            choices.append((abs(aligned - near), aligned, offset))
    if not choices:
        raise PanicOverlayError("no unused executable page can hold the panic renderer")
    _distance, address, offset = min(choices)
    return address, offset


def _choose_state(image: _Image) -> int:
    for segment in image.segments:
        if segment.name != "__DATA":
            continue
        for start, length in _gaps(segment):
            aligned = (start + 63) & ~63
            while aligned + STATE_BYTES <= start + length:
                offset = _va_offset(image, aligned)
                if bytes(image.data[offset : offset + STATE_BYTES]) == bytes(STATE_BYTES):
                    return aligned
                aligned += 64
    raise PanicOverlayError("no unused writable page can hold the panic record")


def _elf_text(path: Path) -> tuple[bytes, dict[str, int]]:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise PanicOverlayError("panic renderer ELF is missing")
    section_off = struct.unpack_from("<Q", data, 40)[0]
    section_size = struct.unpack_from("<H", data, 58)[0]
    section_count = struct.unpack_from("<H", data, 60)[0]
    name_index = struct.unpack_from("<H", data, 62)[0]

    def section(index: int) -> tuple:
        return struct.unpack_from("<IIQQQQIIQQ", data, section_off + index * section_size)

    names = section(name_index)
    def cstr(offset: int) -> str:
        return data[names[4] + offset :].split(b"\0", 1)[0].decode()

    text = None
    symtab = None
    strtab = None
    for index in range(section_count):
        item = section(index)
        name = cstr(item[0])
        if name == ".text":
            text = item
        elif item[1] == 2:
            symtab = item
        elif name == ".strtab":
            strtab = item
    if text is None or symtab is None or strtab is None:
        raise PanicOverlayError("panic renderer ELF has no text or symbols")
    blob = bytearray(data[text[4] : text[4] + text[5]])
    symbols: dict[str, int] = {}
    entry = symtab[9] or 24
    for index in range(symtab[5] // entry):
        name_off, info, _other, shndx, value, _size = struct.unpack_from("<IBBHQQ", data, symtab[4] + index * entry)
        if shndx == 0:
            continue
        name = data[strtab[4] + name_off :].split(b"\0", 1)[0].decode()
        if name and text[3] <= value < text[3] + text[5]:
            symbols[name] = value - text[3]
    if symbols.get("pd_hook") != 0:
        raise PanicOverlayError("panic renderer does not start at pd_hook")
    if len(blob) > PAGE:
        raise PanicOverlayError("panic renderer no longer fits in one page")
    return bytes(blob), symbols


def _patch_quad(blob: bytearray, symbols: dict[str, int], name: str, value: int) -> None:
    offset = symbols[name]
    struct.pack_into("<Q", blob, offset, value)


def install_panic_overlay(kernel: bytes, overlay_elf: Path = OVERLAY_ELF) -> bytes:
    """Return the kernel with the framebuffer panic renderer installed."""

    image = _load(kernel)
    blob, symbols = _elf_text(overlay_elf)
    if _u32(blob, symbols["nested_stolen"]) != NESTED_PROLOGUE:
        raise PanicOverlayError("renderer nested hook is not sub sp, sp, #0x50")
    if _u32(blob, symbols["save_stolen"]) != SAVE_PROLOGUE:
        raise PanicOverlayError("renderer save hook is not the PESavePanicInfo prologue")

    string_va = None
    text = next(segment for segment in image.segments if segment.name == "__TEXT")
    window = image.data[text.fileoff : text.fileoff + text.filesize]
    marker = b"Nested panic detected"
    found = window.find(marker)
    if found < 0 or window.find(marker, found + 1) >= 0:
        raise PanicOverlayError("nested-panic string is missing or repeated")
    string_va = text.vmaddr + found
    xref = _string_xref(image, string_va)
    nested = _function_containing(image.functions, xref)
    if _word(image, nested) != NESTED_PROLOGUE:
        raise PanicOverlayError(f"nested panic at {nested:#x} does not start with sub sp, sp, #0x50")
    save = _require_symbol(image, "_PESavePanicInfo", SAVE_PROLOGUE)
    doprnt = _require_symbol(image, "__doprnt", DOPRNT_PROLOGUE)
    video = _video_address(image)
    cave, cave_off = _choose_cave(image, len(blob), nested)
    state = _choose_state(image)
    if _zero_at(image, state, STATE_BYTES, 64) < 0:
        raise PanicOverlayError("panic record page is not unused")

    patched = bytearray(blob)
    def place(anchor: str, function: str, target_name: str, target: int) -> None:
        _patch_quad(patched, symbols, anchor, cave + symbols[function])
        _patch_quad(patched, symbols, target_name, target)

    place("pd_context_anchor", "pd_context", "pd_context_target", state)
    place("pd_vinfo_anchor", "pd_vinfo", "pd_vinfo_target", video)
    place("pd_doprnt_anchor", "pd_doprnt", "pd_doprnt_target", doprnt)
    place("pd_nested_anchor", "pd_nested_resume", "pd_nested_target", nested + 4)
    place("pd_save_anchor", "pd_save_resume", "pd_save_target", save + 4)
    _patch_quad(patched, symbols, "pd_slide_anchor", cave + symbols["pd_slide"])

    image.data[cave_off : cave_off + len(patched)] = patched
    struct.pack_into("<I", image.data, _va_offset(image, nested), _encode_branch(nested, cave + symbols["pd_hook"]))
    struct.pack_into("<I", image.data, _va_offset(image, save), _encode_branch(save, cave + symbols["pd_save_hook"]))
    print(f"    [panic_overlay] text at {cave:#x}, record at {state:#x}, video at {video:#x}")
    return bytes(image.data)


class PanicOverlayPatch(KernelPatch):
    name = "panic_overlay"
    description = "Draw a panic on the framebuffer when the display is up"

    def apply(self, ctx: PatchContext) -> Path:
        output = ctx.work_dir / "kernel_panic_overlay.raw"
        output.parent.mkdir(parents=True, exist_ok=True)
        patched = install_panic_overlay(ctx.kernel_path.read_bytes())
        if len(patched) != ctx.kernel_path.stat().st_size:
            raise PanicOverlayError("panic overlay changed the kernel size")
        output.write_bytes(patched)
        return output
