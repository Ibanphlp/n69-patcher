"""Minimal defensive reader for 64-bit little-endian Mach-O kernelcaches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct


MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_FILESET_ENTRY = 0x35


class MachOError(ValueError):
    """A kernelcache is not a supported, well-bounded Mach-O file."""


@dataclass(frozen=True)
class MachOHeader:
    cputype: int
    cpusubtype: int
    filetype: int
    ncmds: int
    sizeofcmds: int
    flags: int


@dataclass(frozen=True)
class Segment:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    initprot: int
    nsects: int


@dataclass(frozen=True)
class FileSetEntry:
    identifier: str
    vmaddr: int
    fileoff: int


@dataclass(frozen=True)
class MachOInfo:
    header: MachOHeader
    segments: tuple[Segment, ...]
    filesets: tuple[FileSetEntry, ...]


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def _cstring(command: bytes, offset: int) -> str:
    if offset >= len(command):
        raise MachOError("chaîne de commande Mach-O hors limites")
    end = command.find(b"\0", offset)
    if end == -1:
        raise MachOError("chaîne de commande Mach-O non terminée")
    return command[offset:end].decode("utf-8", "replace")


def inspect_bytes(data: bytes) -> MachOInfo:
    if len(data) < 32:
        raise MachOError("en-tête Mach-O tronqué")
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _reserved = struct.unpack_from("<8I", data)
    if magic != MH_MAGIC_64:
        raise MachOError("Mach-O 64 little-endian attendu")
    commands_start = 32
    commands_end = commands_start + sizeofcmds
    if commands_end > len(data):
        raise MachOError("zone des commandes Mach-O tronquée")

    segments: list[Segment] = []
    filesets: list[FileSetEntry] = []
    cursor = commands_start
    for _ in range(ncmds):
        if cursor + 8 > commands_end:
            raise MachOError("commande Mach-O tronquée")
        command_type, command_size = struct.unpack_from("<2I", data, cursor)
        if command_size < 8 or cursor + command_size > commands_end:
            raise MachOError("taille de commande Mach-O invalide")
        command = data[cursor : cursor + command_size]
        if command_type == LC_SEGMENT_64:
            if command_size < 72:
                raise MachOError("commande de segment trop courte")
            _cmd, _size, raw_name, vmaddr, vmsize, fileoff, filesize, _maxprot, initprot, nsects, _segment_flags = struct.unpack_from(
                "<2I16s4Q4I", command
            )
            if fileoff + filesize > len(data):
                raise MachOError(f"segment {_name(raw_name)} hors limites")
            segments.append(Segment(_name(raw_name), vmaddr, vmsize, fileoff, filesize, initprot, nsects))
        elif command_type == LC_FILESET_ENTRY:
            if command_size < 32:
                raise MachOError("commande de fileset trop courte")
            _cmd, _size, vmaddr, fileoff, entry_id_offset, _reserved = struct.unpack_from("<2I2Q2I", command)
            filesets.append(FileSetEntry(_cstring(command, entry_id_offset), vmaddr, fileoff))
        cursor += command_size
    if cursor != commands_end:
        raise MachOError("nombre ou taille des commandes Mach-O incohérent")
    return MachOInfo(
        header=MachOHeader(cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags),
        segments=tuple(segments),
        filesets=tuple(filesets),
    )


def inspect(path: str | Path) -> MachOInfo:
    return inspect_bytes(Path(path).read_bytes())
