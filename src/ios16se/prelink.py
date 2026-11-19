"""Read the kext bundle identifiers embedded in a kernelcache prelink area."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .macho import MachOError, inspect_bytes as inspect_macho_bytes


class PrelinkError(ValueError):
    """A Mach-O kernelcache has no usable prelink metadata segment."""


@dataclass(frozen=True)
class DriverInventory:
    bundle_identifiers: tuple[str, ...]


@dataclass(frozen=True)
class DriverMetadata:
    bundle_identifier: str
    executable_load_address: int
    executable_size: int
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class DriverRecord:
    executable_load_address: int | None
    executable_size: int | None
    dependencies: tuple[str, ...]
    personalities: tuple[str, ...]


def driver_records(data: bytes) -> dict[str, DriverRecord]:
    """Read the direct kext dictionaries. IDREFs can point outside their record."""
    try:
        root = ET.fromstring(data.rstrip(b"\0"))
    except ET.ParseError as error:
        raise PrelinkError(f"XML de préliaison invalide : {error}") from error
    ids = {}
    for node in root.iter():
        if "ID" in node.attrib:
            identifier = node.attrib["ID"]
            if identifier in ids:
                raise PrelinkError(f"ID XML dupliqué : {identifier}")
            ids[identifier] = node

    def resolve(node):
        seen = set()
        while "IDREF" in node.attrib:
            target = node.attrib["IDREF"]
            if target in seen or target not in ids:
                raise PrelinkError(f"IDREF invalide : {target}")
            seen.add(target)
            node = ids[target]
        return node

    def fields(node):
        node = resolve(node)
        if node.tag != "dict" or len(node) % 2:
            raise PrelinkError("dictionnaire de préliaison invalide")
        result = {}
        for key, value in zip(node[::2], node[1::2]):
            key = resolve(key)
            if key.tag != "key" or not key.text or key.text in result:
                raise PrelinkError("clé de préliaison invalide ou dupliquée")
            result[key.text] = value
        return result

    def integer(node):
        node = resolve(node)
        try:
            value = int(node.text or "", 0)
        except ValueError as error:
            raise PrelinkError("entier de préliaison invalide") from error
        if node.tag != "integer" or value < 0:
            raise PrelinkError("entier de préliaison invalide")
        return value

    if root.tag == "plist" and len(root) == 1:
        root = root[0]
    entries = fields(root).get("_PrelinkInfoDictionary")
    if entries is None or resolve(entries).tag != "array":
        raise PrelinkError("tableau _PrelinkInfoDictionary absent")
    records = {}
    for entry in resolve(entries):
        record = fields(entry)
        if "CFBundleIdentifier" not in record:
            raise PrelinkError("CFBundleIdentifier absent")
        name = resolve(record["CFBundleIdentifier"])
        if name.tag != "string" or not name.text or name.text in records:
            raise PrelinkError(f"CFBundleIdentifier invalide ou dupliqué : {name.text}")
        addr = record.get("_PrelinkExecutableLoadAddr")
        size = record.get("_PrelinkExecutableSize")
        if (addr is None) != (size is None):
            raise PrelinkError(f"plage exécutable incomplète pour {name.text}")
        records[name.text] = DriverRecord(
            integer(addr) if addr is not None else None,
            integer(size) if size is not None else None,
            tuple(sorted(fields(record["OSBundleLibraries"]))) if "OSBundleLibraries" in record else (),
            tuple(sorted(fields(record["IOKitPersonalities"]))) if "IOKitPersonalities" in record else (),
        )
    if not records:
        raise PrelinkError("aucun identifiant de pilote dans les métadonnées préliées")
    return records


def bundle_ids_from_prelink(data: bytes) -> DriverInventory:
    """Read unique bundle identifiers from an XML prelink data region."""

    return DriverInventory(tuple(sorted(driver_records(data))))


def _prelink_region(data: bytes) -> bytes:
    """Locate ``__PRELINK_INFO`` in a validated 64-bit kernelcache Mach-O."""

    try:
        macho = inspect_macho_bytes(data)
    except MachOError as error:
        raise PrelinkError(str(error)) from error
    for segment in macho.segments:
        if segment.name == "__PRELINK_INFO":
            return data[segment.fileoff : segment.fileoff + segment.filesize]
    raise PrelinkError("segment __PRELINK_INFO absent")


def inspect_bytes(data: bytes) -> DriverInventory:
    return bundle_ids_from_prelink(_prelink_region(data))


def inspect_record(path: str | Path, bundle_identifier: str) -> DriverRecord:
    records = driver_records(_prelink_region(Path(path).read_bytes()))
    try:
        return records[bundle_identifier]
    except KeyError as error:
        raise PrelinkError(f"pilote introuvable : {bundle_identifier}") from error


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
    record = inspect_record(source, bundle_identifier)
    if record.executable_size is None:
        raise PrelinkError(f"aucun exécutable déclaré pour {bundle_identifier}")
    info = metadata(source, bundle_identifier)
    if (info.executable_load_address, info.executable_size) != (
        record.executable_load_address, record.executable_size
    ):
        raise PrelinkError(f"la plage d'extraction ne correspond pas à la fiche de {bundle_identifier}")
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
    if destination.exists() and destination.samefile(source):
        raise PrelinkError("la destination désigne le noyau source")
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
