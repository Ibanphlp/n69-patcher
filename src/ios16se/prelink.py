"""Read the kext bundle identifiers embedded in a kernelcache prelink area."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .macho import MachOError, inspect_bytes as inspect_macho_bytes


class PrelinkError(ValueError):
    """A Mach-O kernelcache has no usable prelink metadata segment."""


_IDENTIFIER = re.compile(rb"<key>CFBundleIdentifier</key><string>([^<]+)</string>")


@dataclass(frozen=True)
class DriverInventory:
    bundle_identifiers: tuple[str, ...]


@dataclass(frozen=True)
class DriverMetadata:
    bundle_identifier: str
    executable_load_address: int
    executable_size: int
    dependencies: tuple[str, ...]


def bundle_ids_from_prelink(data: bytes) -> DriverInventory:
    """Read unique bundle identifiers from an XML prelink data region."""

    identifiers = tuple(sorted({item.decode("utf-8", "replace") for item in _IDENTIFIER.findall(data)}))
    if not identifiers:
        raise PrelinkError("aucun identifiant de pilote dans les métadonnées préliées")
    return DriverInventory(identifiers)


def inspect_bytes(data: bytes) -> DriverInventory:
    """Locate ``__PRELINK_INFO`` in a validated 64-bit kernelcache Mach-O."""

    try:
        macho = inspect_macho_bytes(data)
    except MachOError as error:
        raise PrelinkError(str(error)) from error
    for segment in macho.segments:
        if segment.name == "__PRELINK_INFO":
            return bundle_ids_from_prelink(data[segment.fileoff : segment.fileoff + segment.filesize])
    raise PrelinkError("segment __PRELINK_INFO absent")


def inspect(path: str | Path) -> DriverInventory:
    return inspect_bytes(Path(path).read_bytes())


def metadata(path: str | Path, bundle_identifier: str) -> DriverMetadata:
    """Read the prelinked executable range and OSBundle dependencies of one kext.

    Apple’s prelink XML uses IDREF values and cannot be parsed as a normal
    plist. This deliberately reads only the local kext record needed by the
    porting pipeline.
    """
    data = Path(path).read_bytes()
    try:
        macho = inspect_macho_bytes(data)
    except MachOError as error:
        raise PrelinkError(str(error)) from error
    prelink = next((segment for segment in macho.segments if segment.name == "__PRELINK_INFO"), None)
    if prelink is None:
        raise PrelinkError("segment __PRELINK_INFO absent")
    xml = data[prelink.fileoff : prelink.fileoff + prelink.filesize]
    needle = b"<key>CFBundleIdentifier</key><string>" + bundle_identifier.encode() + b"</string>"
    for match in re.finditer(re.escape(needle), xml):
        record = xml[max(0, match.start() - 8192) : match.end() + 16384]
        address = re.search(rb"<key>_PrelinkExecutableLoadAddr</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>", record)
        size = re.search(rb"<key>_PrelinkExecutableSize</key><integer[^>]*>(0x[0-9a-fA-F]+)</integer>", record)
        libraries = re.search(rb"<key>OSBundleLibraries</key><dict>(.*?)</dict>", record, re.DOTALL)
        if address and size and libraries:
            dependencies = tuple(sorted(set(re.findall(rb"<key>([^<]+)</key><string>", libraries.group(1)))))
            return DriverMetadata(
                bundle_identifier=bundle_identifier,
                executable_load_address=int(address.group(1), 16),
                executable_size=int(size.group(1), 16),
                dependencies=tuple(item.decode("utf-8", "replace") for item in dependencies),
            )
    raise PrelinkError(f"métadonnées préliées introuvables pour {bundle_identifier}")


def extract_driver(path: str | Path, bundle_identifier: str, output: str | Path) -> Path:
    """Extract one prelinked kext Mach-O without modifying its source cache."""
    source = Path(path)
    info = metadata(source, bundle_identifier)
    macho = inspect_macho_bytes(source.read_bytes())
    segment = next(
        (
            item
            for item in macho.segments
            if item.vmaddr <= info.executable_load_address
            and info.executable_load_address + info.executable_size <= item.vmaddr + item.filesize
        ),
        None,
    )
    if segment is None:
        raise PrelinkError(f"plage exécutable non extraible pour {bundle_identifier}")
    offset = segment.fileoff + info.executable_load_address - segment.vmaddr
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        input_file.seek(offset)
        remaining = info.executable_size
        while remaining:
            block = input_file.read(min(remaining, 1024 * 1024))
            if not block:
                raise PrelinkError(f"lecture tronquée pour {bundle_identifier}")
            output_file.write(block)
            remaining -= len(block)
    return destination


def delta(target: DriverInventory, donor: DriverInventory) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return bundle identifiers only in target, then only in donor."""

    target_ids = set(target.bundle_identifiers)
    donor_ids = set(donor.bundle_identifiers)
    return tuple(sorted(target_ids - donor_ids)), tuple(sorted(donor_ids - target_ids))
